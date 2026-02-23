"""
Shared LLM Client — Groq (primary) + Ollama (fallback)

Used by both blue_h2_intelligence.py and fid_probability.py
to avoid circular imports and code duplication.

Usage:
    from llm_client import llm_client          # singleton
    backend = llm_client.check()               # 'groq', 'ollama', ''
    text = llm_client.generate(system, prompt)  # Groq → Ollama fallback
"""

import os
import time
import logging
from collections import deque
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_HERE = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Groq cloud (fast, free tier: 30 req/min)
_GROQ_KEY_FILE = _HERE / 'groq_api_token.txt'

GROQ_MODEL = 'llama-3.3-70b-versatile'
GROQ_TIMEOUT = 60

# Ollama local fallback
OLLAMA_MODEL = 'llama3.1:8b'
OLLAMA_URL = 'http://localhost:11434/api/generate'
OLLAMA_TIMEOUT = 300   # 5 min — with 6K char text limit, CPU should finish in <3 min


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

class RateLimiter:
    """Simple sliding-window rate limiter."""

    def __init__(self, calls_per_minute: int):
        self._calls: deque = deque()
        self._limit = calls_per_minute

    def wait_if_needed(self):
        now = time.time()
        # Remove calls older than 60 seconds
        while self._calls and self._calls[0] < now - 60:
            self._calls.popleft()
        if len(self._calls) >= self._limit:
            sleep_time = 60 - (now - self._calls[0]) + 0.1
            if sleep_time > 0:
                logger.info(f"Rate limit: sleeping {sleep_time:.1f}s")
                time.sleep(sleep_time)
        self._calls.append(time.time())


# ---------------------------------------------------------------------------
# LLM Client
# ---------------------------------------------------------------------------

class LLMClient:
    """Dual-backend LLM client: Groq primary → Ollama fallback.

    Attributes:
        total_tokens: cumulative tokens consumed (Groq only; Ollama unreported)
        total_calls: number of generate() calls made
    """

    def __init__(self):
        self._backend: Optional[str] = None   # None=not checked, 'groq', 'ollama', False
        self._groq_key = self._load_groq_key()
        self._rate_limiter = RateLimiter(28)   # 28/min to stay safely under Groq's 30
        self._groq_resume_at = None            # set when daily limit hit
        self._groq_disabled = False            # set True on 401 (invalid key)
        self.total_tokens: int = 0
        self.total_calls: int = 0

    # ----- key loading -----

    @staticmethod
    def _load_groq_key() -> str:
        key = ''
        if _GROQ_KEY_FILE.exists():
            try:
                key = _GROQ_KEY_FILE.read_text().strip()
            except Exception:
                pass
        if not key:
            key = os.environ.get('GROQ_API_KEY', '')
        return key

    # ----- backend check -----

    def check(self) -> str:
        """Check which backend is available. Returns 'groq', 'ollama', or ''."""
        if self._backend is not None:
            return self._backend if self._backend else ''

        # Try Groq first
        if self._groq_key:
            try:
                from groq import Groq
                client = Groq(api_key=self._groq_key)
                resp = client.chat.completions.create(
                    model=GROQ_MODEL,
                    messages=[{'role': 'user', 'content': 'Say OK'}],
                    max_tokens=5, temperature=0)
                if resp.choices:
                    self._backend = 'groq'
                    print(f"  LLM: Groq ({GROQ_MODEL})")
                    return 'groq'
            except Exception as e:
                print(f"  Groq unavailable: {e}")

        # Fallback: Ollama
        try:
            resp = requests.get('http://localhost:11434/api/tags', timeout=5)
            if resp.status_code == 200:
                models = [m['name'] for m in resp.json().get('models', [])]
                if any(OLLAMA_MODEL in m for m in models):
                    self._backend = 'ollama'
                    print(f"  LLM: Ollama ({OLLAMA_MODEL})")
                    return 'ollama'
                else:
                    print(f"  Ollama running but {OLLAMA_MODEL} not found.")
        except Exception:
            pass

        self._backend = False
        print("  WARNING: No LLM available (set GROQ_API_KEY or pull Ollama model).")
        return ''

    # ----- generation -----

    def generate(self, system_prompt: str, user_prompt: str,
                 max_tokens: int = 1500) -> str:
        """Generate text. Tries Groq first, falls back to Ollama.

        Args:
            system_prompt: system-level instruction
            user_prompt: the actual query
            max_tokens: max output tokens

        Returns:
            Generated text, or '' if both backends fail.
        """
        backend = self.check()
        self.total_calls += 1

        # -- Groq (skip if disabled or daily limit hit until reset) --
        groq_ok = (backend == 'groq' or self._groq_key) and not self._groq_disabled
        if groq_ok and self._groq_resume_at:
            from datetime import datetime as _dt, timezone as _tz
            if _dt.now(_tz.utc) >= self._groq_resume_at:
                self._groq_resume_at = None  # reset expired, try Groq again
                print("  Groq daily limit reset — switching back to Groq.")
            else:
                groq_ok = False  # still rate-limited, skip straight to Ollama
        if groq_ok:
            result = self._try_groq(system_prompt, user_prompt, max_tokens)
            if result is not None:
                return result
            # Fall through to Ollama

        # -- Ollama --
        result = self._try_ollama(system_prompt, user_prompt, max_tokens)
        if result is not None:
            return result

        return ''

    def _try_groq(self, system_prompt: str, user_prompt: str,
                  max_tokens: int) -> Optional[str]:
        try:
            from groq import Groq
            self._rate_limiter.wait_if_needed()
            client = Groq(api_key=self._groq_key)
            resp = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {'role': 'system', 'content': system_prompt},
                    {'role': 'user', 'content': user_prompt}
                ],
                max_tokens=max_tokens,
                temperature=0.2)
            # Track token usage
            if hasattr(resp, 'usage') and resp.usage:
                self.total_tokens += getattr(resp.usage, 'total_tokens', 0)
            return resp.choices[0].message.content or ''
        except Exception as e:
            err_str = str(e)
            logger.warning(f"Groq error: {e}")
            # Auth error — disable Groq entirely (key invalid/revoked)
            if '401' in err_str or 'auth' in err_str.lower() or 'invalid' in err_str.lower():
                self._groq_disabled = True
                print(f"  Groq API key invalid (401) — disabled for this session. "
                      f"Using Ollama only.")
            # Daily rate limit — use Ollama until next UTC midnight reset
            elif '429' in err_str and 'per day' in err_str:
                from datetime import datetime as _dt, timezone as _tz, timedelta as _td
                now_utc = _dt.now(_tz.utc)
                self._groq_resume_at = (now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
                                        + _td(days=1))
                remaining = self._groq_resume_at - now_utc
                hrs = remaining.seconds // 3600
                mins = (remaining.seconds % 3600) // 60
                print(f"  Groq daily limit reached — using Ollama until reset "
                      f"(~{hrs}h {mins}m).")
            else:
                print(f"  Groq error: {e} — falling back to Ollama...")
            return None

    def _try_ollama(self, system_prompt: str, user_prompt: str,
                    max_tokens: int) -> Optional[str]:
        try:
            payload = {
                'model': OLLAMA_MODEL,
                'prompt': f"{system_prompt}\n\n{user_prompt}",
                'stream': False,
                'options': {
                    'temperature': 0.2,
                    'num_predict': max_tokens,
                    'num_ctx': 8192,   # llama3.1 supports up to 128K; 8K is safe on CPU
                }
            }
            resp = requests.post(OLLAMA_URL, json=payload, timeout=OLLAMA_TIMEOUT)
            if resp.status_code == 200:
                return resp.json().get('response', '')
        except Exception as e:
            logger.warning(f"Ollama error: {e}")
            print(f"  Ollama error: {e}")
        return None


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

llm_client = LLMClient()
