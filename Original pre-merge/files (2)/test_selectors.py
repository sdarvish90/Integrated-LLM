#!/usr/bin/env python3
"""
Test different selector syntaxes
"""

from playwright.sync_api import sync_playwright

print("=" * 70)
print("TESTING DIFFERENT SELECTORS")
print("=" * 70)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    page = browser.new_page()
    
    print("\n1. Loading page...")
    page.goto("https://www.irena.org/Publications", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(5000)
    
    print("\n2. Testing different selectors...\n")
    
    selectors_to_test = [
        # Various quote and escape combinations
        "a[href*='/Publications/']",
        'a[href*="/Publications/"]',
        "a[href*='Publications']",
        'a[href*="Publications"]',
        "a[href*=Publications]",
        'a[href^="/Publications"]',
        'a[href^="/Publications/"]',
        "a",
    ]
    
    for sel in selectors_to_test:
        try:
            elements = page.query_selector_all(sel)
            count = len(elements)
            print(f"   {sel!r:40} -> {count} elements")
            
            # If we found some, show first few hrefs
            if count > 0 and count < 300:
                for el in elements[:3]:
                    href = el.get_attribute('href')
                    print(f"      Example: {href}")
        except Exception as e:
            print(f"   {sel!r:40} -> ERROR: {e}")
    
    print("\n3. Try using locator instead of query_selector_all...")
    try:
        locator = page.locator('a[href*="Publications"]')
        count = locator.count()
        print(f"   locator('a[href*=\"Publications\"]').count() = {count}")
        
        if count > 0:
            for i in range(min(5, count)):
                href = locator.nth(i).get_attribute('href')
                print(f"      {i+1}. {href}")
    except Exception as e:
        print(f"   ERROR: {e}")
    
    print("\n4. Try eval_on_selector_all (JavaScript)...")
    try:
        results = page.eval_on_selector_all(
            'a',
            '''els => els.filter(a => a.href && a.href.includes('/Publications/')).map(a => a.href)'''
        )
        print(f"   JavaScript filter found: {len(results)} hrefs")
        for href in results[:5]:
            print(f"      {href}")
    except Exception as e:
        print(f"   ERROR: {e}")
    
    print("\n5. Try evaluate (pure JavaScript)...")
    try:
        results = page.evaluate('''
            () => {
                const links = document.querySelectorAll('a[href*="/Publications/"]');
                return Array.from(links).map(a => a.getAttribute('href'));
            }
        ''')
        print(f"   document.querySelectorAll found: {len(results)} hrefs")
        for href in results[:5]:
            print(f"      {href}")
    except Exception as e:
        print(f"   ERROR: {e}")
    
    input("\nPress Enter to close...")
    browser.close()
