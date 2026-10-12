"""Optional browser QA; requires already-installed Playwright and Chromium.
The clipping engine itself has no third-party dependencies.
"""
import json
import tempfile
from pathlib import Path
from playwright.sync_api import sync_playwright
from clipbeta.engine import read_json, apply_review, export_plan, reconcile

ROOT=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as d, sync_playwright() as pw:
    browser=pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1280,'height':1000},accept_downloads=True)
    errors=[]; page.on('pageerror',lambda err:errors.append(str(err)))
    page.goto((ROOT/'samples/review.html').as_uri())
    assert page.locator('article').count()==5
    assert '5 件 未確認' in page.locator('#count').inner_text()
    page.locator('#adopt').click();page.locator('#adopt').click()
    assert '0 件 未確認' in page.locator('#count').inner_text()
    attack='</script><img src=x onerror="window.pwned=true">'
    page.locator('textarea').first.fill(attack)
    assert page.locator('.preview').first.inner_text()==attack
    assert page.locator('img').count()==0
    assert page.evaluate('window.pwned') is None
    page.locator('article').nth(1).locator('select').first.select_option('rejected')
    with page.expect_download() as event:page.locator('#save').click()
    path=Path(d)/'review.json'; event.value.save_as(path)
    plan=export_plan(apply_review(read_json(ROOT/'samples/proposal.json'),read_json(path)))
    assert len(plan['operations'])==4
    assert plan['operations'][0]['text']==attack
    assert not reconcile(plan,plan)['create']
    page.locator('#reject').click(); assert page.locator('select').first.input_value()=='rejected'
    page.reload(); assert page.locator('select').first.input_value()=='pending'
    page.screenshot(path=str(ROOT/'samples/review-preview.png'),full_page=True)
    assert not errors,errors
    browser.close()
print('PASS: render, repeat-click, edit, reject, download, validate/export, safe text, reload reset; no JS errors')
