#!/usr/bin/env python3
"""
Test script to find and click IRENA download button
"""

from playwright.sync_api import sync_playwright
import time

url = "https://www.irena.org/Publications/2026/Jan/Renewable-energy-auctions-Design-for-risk-allocation"

print(f"Testing: {url}\n")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context(accept_downloads=True)
    page = context.new_page()
    
    print("1. Opening page...")
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(5000)
    
    # Screenshot before
    page.screenshot(path="irena_before_click.png")
    print("   Screenshot saved: irena_before_click.png")
    
    print("\n2. Looking for 'Download full report' text...")
    
    # Find all elements containing "download"
    elements = page.evaluate("""
        () => {
            const all = document.querySelectorAll('*');
            const results = [];
            for (const el of all) {
                const text = (el.innerText || '').toLowerCase();
                if (text.includes('download') && text.length < 100) {
                    results.push({
                        tag: el.tagName,
                        text: el.innerText.substring(0, 80),
                        className: el.className || '',
                        id: el.id || ''
                    });
                }
            }
            return results.slice(0, 20);
        }
    """)
    
    print(f"   Found {len(elements)} elements with 'download':")
    for el in elements:
        print(f"      <{el['tag']}> class='{el['className'][:30]}' text='{el['text'][:50]}'")
    
    print("\n3. Trying to find and hover over download button...")
    
    # Try different ways to find the download button
    found = False
    
    # Method 1: Look for text "Download full report"
    try:
        btn = page.get_by_text("Download full report", exact=False).first
        if btn.is_visible():
            print("   Found via get_by_text('Download full report')")
            print("   Hovering...")
            btn.hover()
            page.wait_for_timeout(2000)
            page.screenshot(path="irena_after_hover.png")
            print("   Screenshot saved: irena_after_hover.png")
            found = True
    except Exception as e:
        print(f"   get_by_text failed: {e}")
    
    # Method 2: Look for any element with "download" and "report"
    if not found:
        try:
            btn = page.locator("text=/download.*report/i").first
            if btn.is_visible():
                print("   Found via regex locator")
                btn.hover()
                page.wait_for_timeout(2000)
                page.screenshot(path="irena_after_hover.png")
                found = True
        except Exception as e:
            print(f"   Regex locator failed: {e}")
    
    print("\n4. Looking for 'PDF' option after hover...")
    
    # Look for PDF link/button
    pdf_elements = page.evaluate("""
        () => {
            const all = document.querySelectorAll('a, button, span, div');
            const results = [];
            for (const el of all) {
                const text = (el.innerText || '').trim();
                if (text === 'PDF' || text.includes('PDF')) {
                    const rect = el.getBoundingClientRect();
                    results.push({
                        tag: el.tagName,
                        text: text.substring(0, 50),
                        className: el.className || '',
                        href: el.getAttribute('href') || '',
                        visible: rect.width > 0 && rect.height > 0,
                        top: rect.top,
                        left: rect.left
                    });
                }
            }
            return results;
        }
    """)
    
    print(f"   Found {len(pdf_elements)} elements with 'PDF':")
    for el in pdf_elements:
        print(f"      <{el['tag']}> visible={el['visible']} text='{el['text']}' href='{el['href'][:50] if el['href'] else ''}'")
    
    print("\n5. Trying to click on PDF...")
    
    # Try to click on PDF
    try:
        # First try direct link
        pdf_link = page.locator("a:has-text('PDF')").first
        if pdf_link.is_visible():
            print("   Found PDF link, setting up download handler...")
            
            with page.expect_download(timeout=60000) as download_info:
                pdf_link.click()
                print("   Clicked! Waiting for download...")
            
            download = download_info.value
            print(f"   Download started: {download.suggested_filename}")
            
            # Wait for it to finish
            path = download.path()
            print(f"   Downloaded to: {path}")
            
            # Save to current directory
            download.save_as(f"./{download.suggested_filename}")
            print(f"   Saved as: {download.suggested_filename}")
            
    except Exception as e:
        print(f"   Click failed: {e}")
    
    print("\n6. Taking final screenshot...")
    page.screenshot(path="irena_final.png")
    
    input("\nPress Enter to close browser...")
    browser.close()