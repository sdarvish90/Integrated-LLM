# Hydrogen Intelligence Monitoring System

Automated news collection and analysis for hydrogen project intelligence.

## Quick Start

### 1. Install Dependencies

```bash
# Install Python packages
pip install -r requirements.txt
```

### 2. Run the System

```bash
python hydrogen_intelligence_monitor.py
```

### 3. Choose Mode

When you run the script, you'll see:

```
CHOOSE MODE:
1. Run once (test mode)          ← Start here to test
2. Run scheduled monitoring (continuous)
3. View recent articles
4. Generate daily digest
5. Generate weekly summary
6. View statistics
```

**Recommended first run:** Choose option `1` to test

---

## What It Does

### Monitors These Sources 24/7:
- Hydrogen Insight
- Recharge News
- Fuel Cells Works
- H2 View
- Energy Voice
- PV Magazine
- Green Hydrogen News

### Tracks These Keywords:
**CRITICAL (10 points):**
- FID, final investment decision
- Offtake agreement, binding contract
- 45V, OBBB

**HIGH (5 points):**
- Oman, Chile, Houston (regions)
- BP, Shell, Yara (companies)
- Electrolyzer orders, PPAs

**MEDIUM (2 points):**
- Cost reduction, forecasts
- Technology partnerships

**LOW (1 point):**
- Research, pilot projects

### Saves Everything To Database:
- Articles with keywords
- Priority scores
- Regional categorization
- Keyword matches

---

## Example Output

When you run the script, you'll see:

```
======================================================================
Checking RSS feeds at 2026-01-21 14:30:00
======================================================================

Checking Hydrogen Insight...

  🔔 CRITICAL PRIORITY (15 points)
     Title: BP reaches FID on Oman hydrogen project with Yara offtake
     Region: Oman
     Keywords: FID, Oman, offtake agreement, Yara
     URL: https://...

Checking Recharge...

  🔔 HIGH PRIORITY (7 points)
     Title: Chile hydrogen costs drop 15% as PPA prices fall
     Region: Chile
     Keywords: Chile, PPA, cost reduction
     URL: https://...

======================================================================
Summary: Checked 80 articles, saved 12 new articles
======================================================================
```

---

## View Collected Data

### Option 3: View Recent Articles

Run the script and choose option 3:

```
How many days back? 7
Minimum priority score? 5
```

Output:
```
======================================================================
ARTICLES FROM LAST 7 DAY(S) (Score >= 5)
======================================================================

[CRITICAL] ACME reaches financial close on $1.2B Oman project
  Source: Hydrogen Insight | Region: Oman | Score: 15
  Collected: 2026-01-20 10:23:15
  URL: https://...

[HIGH] Houston developers delay projects amid PPA cost surge
  Source: Recharge | Region: Houston | Score: 7
  Collected: 2026-01-19 15:42:03
  URL: https://...

Total: 12 articles
```

---

## Daily Digest (Option 4)

Generates summary of today's high-priority news:

```
======================================================================
HYDROGEN INTELLIGENCE DAILY DIGEST - 2026-01-21
======================================================================

🔴 CRITICAL ALERTS
----------------------------------------------------------------------

BP announces FID on Oman HyPort project with $800M investment
   Source: Hydrogen Insight
   Region: Oman
   Keywords: FID, BP, Oman, offtake agreement
   URL: https://...

🟡 HIGH PRIORITY
----------------------------------------------------------------------

Chile announces new $500M incentive package for green hydrogen
   Source: Recharge | Region: Chile
   URL: https://...

Electrolyzer costs fall to $750/kW as manufacturers scale production
   Source: PV Magazine | Region: Global
   URL: https://...

======================================================================
Total high-priority articles: 8
======================================================================
```

---

## Weekly Summary (Option 5)

Activity trends and top stories:

```
======================================================================
HYDROGEN INTELLIGENCE WEEKLY SUMMARY
Week ending: 2026-01-21
======================================================================

📊 ACTIVITY BY REGION & CATEGORY
----------------------------------------------------------------------
Oman                 | HIGH       |  15 articles
Chile                | MEDIUM     |  12 articles
Houston              | CRITICAL   |   8 articles
Global               | LOW        |  45 articles

🏆 TOP 10 STORIES THIS WEEK
----------------------------------------------------------------------

1. ACME Duqm begins production - first commercial green hydrogen in Oman
   Hydrogen Insight | Oman | Priority: 20
   https://...

2. US 45V deadline triggers wave of Houston project cancellations
   Recharge | Houston | Priority: 18
   https://...

[continues...]

======================================================================
```

---

## Statistics (Option 6)

Database overview:

```
======================================================================
DATABASE STATISTICS
======================================================================

Total articles: 247

By Category:
  HIGH           :   89
  MEDIUM         :   76
  CRITICAL       :   45
  LOW            :   37

By Region (Top 10):
  Oman                :   67
  Chile               :   45
  Houston             :   38
  Global              :   34
  Europe              :   28

By Source:
  Hydrogen Insight            :   89
  Fuel Cells Works            :   67
  Recharge                    :   45
  H2 View                     :   32
  Energy Voice                :   14
```

---

## Scheduled Monitoring (Option 2)

Run continuously with automated checks:

```bash
python hydrogen_intelligence_monitor.py
# Choose option 2

[CONTINUOUS MODE] Starting scheduled monitoring...
Press Ctrl+C to stop

======================================================================
SCHEDULER CONFIGURED
======================================================================
• RSS monitoring: Every 3 hours
• Daily digest: Every day at 09:00
• Weekly summary: Every Monday at 09:00
======================================================================
```

The system will:
- Check RSS feeds every 3 hours
- Generate daily digest at 9 AM
- Generate weekly summary every Monday at 9 AM
- Save all articles to database
- Alert on critical news

---

## Database Location

All data saved to: `hydrogen_intelligence.db`

You can query it directly with SQLite:

```bash
sqlite3 hydrogen_intelligence.db

# Example queries:
SELECT * FROM articles WHERE category = 'CRITICAL';
SELECT region, COUNT(*) FROM articles GROUP BY region;
SELECT * FROM articles WHERE keywords LIKE '%Oman%' ORDER BY priority_score DESC;
```

---

## Email Alerts (Optional)

To enable email alerts, edit the script:

```python
EMAIL_CONFIG = {
    'enabled': True,  # Change to True
    'smtp_server': 'smtp.gmail.com',
    'smtp_port': 587,
    'sender_email': 'your_email@gmail.com',
    'sender_password': 'your_app_password',  # Gmail: Use App Password
    'recipient_email': 'recipient@example.com',
}
```

**Gmail Setup:**
1. Enable 2-factor authentication
2. Generate App Password: https://myaccount.google.com/apppasswords
3. Use App Password in script (not your regular password)

---

## Customization

### Add More RSS Feeds

Edit in script:

```python
RSS_FEEDS = {
    'Your New Source': 'https://example.com/feed',
    # Add more feeds here
}
```

### Adjust Keywords

Edit keyword categories:

```python
KEYWORDS = {
    'CRITICAL': [
        'your critical keyword',
        'another important term',
    ],
}
```

### Change Monitoring Frequency

Edit scheduling:

```python
# Check every hour instead of 3 hours:
schedule.every(1).hours.do(run_monitoring_cycle)

# Daily digest at different time:
schedule.every().day.at("08:00").do(generate_daily_digest)
```

---

## Troubleshooting

### "No entries found" for a feed

Some feeds may be temporarily down or require headers. The script will skip and continue with other feeds.

### Email not sending

- Check your email/password are correct
- Gmail: Use App Password, not regular password
- Some email providers block SMTP - try Gmail

### Database locked error

Only run one instance at a time. If running scheduled mode, don't also run manual queries.

---

## Next Steps

1. **Test it:** Run option 1 to see it work
2. **Review data:** Run option 3 to see collected articles
3. **Customize:** Add your priority keywords
4. **Deploy:** Run option 2 on a server for 24/7 monitoring

---

## File Structure

```
hydrogen_intelligence_monitor.py  ← Main script
requirements.txt                  ← Python dependencies
hydrogen_intelligence.db          ← SQLite database (created automatically)
README.md                         ← This file
```

---

## Tips

- Run in **test mode** (option 1) first to verify it works
- Check for new articles after 3-4 hours (feeds update at different times)
- Use **option 3** frequently to review what's being collected
- Adjust keyword scores based on what you find most valuable
- The database persists - you can stop/start the script without losing data

---

## Support

Issues you might encounter:

**Q: Nothing is being collected**
A: Some feeds update slowly. Wait 6-12 hours and run again. Also check if keywords match - try lowering min_score to 0.

**Q: Too many articles**
A: Increase minimum score threshold or refine keywords to be more specific.

**Q: Missing important news**
A: Add relevant keywords to HIGH or CRITICAL categories.

**Q: Want to track specific companies/projects**
A: Add them to the KEYWORDS dictionary under appropriate priority level.
