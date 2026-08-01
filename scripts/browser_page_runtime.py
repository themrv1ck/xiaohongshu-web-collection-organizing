#!/usr/bin/env python3
"""Playwright page operations with no process-launch responsibilities."""


def run_page_javascript(page, script: str):
    """Run JavaScript in an already-authorized Playwright page."""
    return page.evaluate(script)
