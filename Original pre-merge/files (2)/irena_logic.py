#!/usr/bin/env python3
"""
Test script to:
1. Click IRENA download button
2. Wait for PDF to open in new tab (Chrome PDF viewer)
3. Click the download button in Chrome's PDF viewer
"""

from playwright.sync_api import sync_playwright
import os
import time

url = "https://www.irena.org/Publications/2026/Jan/Renewable-energy-auctions-Design-for-risk-allocation"
output_folder = "./test_downloads"

print(f"Testing: {url}\n")

os.makedirs(output_folder, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=False,
        downloads_path=output_folder  # Set download directory
    )
    context = browser.new_context(accept_downloads=True)
    page = context.new_page()
    
    print("1. Opening page...")
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(5000)
    
    print("\n2. Looking for 'Download full report'...")
    found = False
    
    try:
        btn = page.get_by_text("Download full report", exact=False).first
        if btn.is_visible():
            print("   Found via get_by_text('Download full report')")
            print("   Hovering...")
            btn.hover()
            page.wait_for_timeout(2000)
            found = True
    except Exception as e:
        print(f"   get_by_text failed: {e}")
    
    if not found:
        try:
            btn = page.locator("text=/download.*report/i").first
            if btn.is_visible():
                print("   Found via regex locator")
                btn.hover()
                page.wait_for_timeout(2000)
                found = True
        except Exception as e:
            print(f"   Regex locator failed: {e}")
    
    if not found:
        print("   ERROR: No download button found!")
        browser.close()
        exit(1)
    
    print("\n3. Looking for PDF link...")
    pdf_link = page.locator("a:has-text('PDF')").first
    
    if not pdf_link.is_visible():
        print("   PDF link not visible, trying to click download button...")
        btn.click()
        page.wait_for_timeout(2000)
        pdf_link = page.locator("a:has-text('PDF')").first
    
    if not pdf_link.is_visible():
        print("   ERROR: PDF link not found!")
        browser.close()
        exit(1)
    
    print("   Found PDF link!")
    
    # Get the href before clicking
    href = pdf_link.get_attribute('href')
    print(f"   PDF href: {href}")
    
    print("\n4. Clicking PDF link and waiting for new tab...")
    
    # Wait for new page (tab) to open when we click
    with context.expect_page(timeout=60000) as new_page_info:
        pdf_link.click()
    
    new_page = new_page_info.value
    print(f"   New tab opened!")
    
    # Wait for the PDF to load in Chrome's PDF viewer
    print("   Waiting 10 seconds for PDF to load in Chrome viewer...")
    new_page.wait_for_timeout(10000)
    
    pdf_url = new_page.url
    print(f"   PDF URL: {pdf_url}")
    
    # Extract filename from URL
    filename = pdf_url.split('/')[-1]
    if '?' in filename:
        filename = filename.split('?')[0]
    if not filename.endswith('.pdf'):
        filename = filename + '.pdf'
    print(f"   Filename: {filename}")
    
    print("\n5. Looking for download button in Chrome PDF viewer...")
    
    # Chrome's PDF viewer uses a shadow DOM, so we need to access it differently
    # The download button is inside the PDF viewer's toolbar
    
    # Method 1: Try keyboard shortcut Ctrl+S
    print("   Trying Ctrl+S keyboard shortcut...")
    
    try:
        with context.expect_page(timeout=5000) as save_dialog:
            new_page.keyboard.press("Control+s")
    except:
        pass
    
    # Method 2: Try to click the download button in the PDF toolbar
    # The PDF viewer toolbar has a download button with id="download" or aria-label containing "download"
    print("   Looking for download button in PDF toolbar...")
    
    # Try clicking by using JavaScript to find the download button in shadow DOM
    try:
        # Chrome PDF viewer has elements in shadow DOM
        new_page.evaluate("""
            () => {
                // Try to find download button
                const viewer = document.querySelector('embed[type="application/pdf"]');
                if (viewer) {
                    console.log('Found PDF embed');
                }
                
                // Try clicking any download-related element
                const downloadBtns = document.querySelectorAll('[id*="download"], [aria-label*="download"], [title*="Download"]');
                console.log('Found download buttons:', downloadBtns.length);
                if (downloadBtns.length > 0) {
                    downloadBtns[0].click();
                }
            }
        """)
    except Exception as e:
        print(f"   JavaScript approach failed: {e}")
    
    # Method 3: Use keyboard shortcut Ctrl+Shift+S or just trigger download via URL
    print("   Trying direct download approach...")
    
    # Set up download handler
    try:
        with new_page.expect_download(timeout=30000) as download_info:
            # Press Ctrl+S to trigger save dialog / download
            new_page.keyboard.press("Control+s")
            print("   Pressed Ctrl+S, waiting for download...")
        
        download = download_info.value
        print(f"   Download triggered: {download.suggested_filename}")
        
        # Save the file
        save_path = os.path.join(output_folder, download.suggested_filename or filename)
        download.save_as(save_path)
        print(f"   Saved to: {save_path}")
        
    except Exception as e:
        print(f"   Ctrl+S download failed: {e}")
        
        # Method 4: Get the PDF URL and download it directly using the page context
        print("\n   Trying to fetch PDF directly from URL...")
        
        try:
            # The PDF is already loaded, let's get its content via fetch
            pdf_data = new_page.evaluate("""
                async (url) => {
                    const response = await fetch(url, { credentials: 'include' });
                    const buffer = await response.arrayBuffer();
                    return Array.from(new Uint8Array(buffer));
                }
            """, pdf_url)
            
            # Convert to bytes
            data = bytes(pdf_data)
            print(f"   Got {len(data)} bytes via fetch")
            
            if data.startswith(b'%PDF'):
                save_path = os.path.join(output_folder, filename)
                with open(save_path, 'wb') as f:
                    f.write(data)
                print(f"   Saved to: {save_path}")
            else:
                print(f"   Not a valid PDF")
                
        except Exception as e2:
            print(f"   Fetch approach failed: {e2}")
    
    print("\n6. Done!")
    
    input("\nPress Enter to close browser...")
    browser.close()

print(f"\nCheck {output_folder} for the downloaded PDF")