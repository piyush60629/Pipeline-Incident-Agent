"""Open the Streamlit app in a real browser so it stays awake."""

import os
import re
import sys

from playwright.sync_api import sync_playwright

APP_URL = os.environ["APP_URL"]

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.goto(APP_URL, wait_until="domcontentloaded", timeout=90_000)
    page.wait_for_timeout(10_000)

    wake_button = page.get_by_role("button", name=re.compile("get this app back up", re.I))
    if wake_button.count() > 0:
        print("App was asleep. Waking it up...")
        wake_button.first.click()
        page.wait_for_timeout(60_000)  # give it time to boot
        if page.get_by_role("button", name=re.compile("get this app back up", re.I)).count() > 0:
            print("App did not wake up.")
            sys.exit(1)
        print("App is awake now.")
    else:
        print("App is already awake.")

    browser.close()
