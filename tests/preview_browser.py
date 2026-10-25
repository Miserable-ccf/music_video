"""Browser integration: local template, persistence, font search and offline requests.

Requires playwright (development only) and a disposable server on port 18765.
"""
import io
import sys
import wave
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

base=sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:18765'
audio=io.BytesIO()
with wave.open(audio,'wb') as stream:
    stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(8000);stream.writeframes(b'\0\0'*8000*2)
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path='/usr/bin/google-chrome',args=['--no-sandbox','--disable-gpu'])
    context=browser.new_context(viewport={'width':1440,'height':1080})
    requests=[];errors=[]
    def restrict(route):
        requests.append(route.request.url)
        if route.request.url.startswith(base+'/'):route.continue_()
        else:route.abort()
    context.route('**/*',restrict)
    page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
    page.goto(base);page.wait_for_load_state('networkidle')
    page.locator('#audioFile').set_input_files({'name':'sample.wav','mimeType':'audio/wav','buffer':audio.getvalue()})
    page.locator('#title').fill('風起雲湧');page.locator('#artist').fill('測試歌手');page.locator('#createJob').click()
    expect(page.locator('#direct')).to_have_class('panel active')
    page.locator('#lyricEditor').fill('[00:00.00]於是我們繼續\n[00:01.00]繁體書法歌詞\n')
    page.locator('#editedConfirmed').check();page.locator('#approveEdited').click()
    expect(page.locator('#preview')).to_have_class('panel active')
    expect(page.locator('#titleFont option')).to_have_count(58)
    page.locator('#fontSearch').fill('正風')
    assert page.locator('#titleFont option').count()==6  # Five matches plus current selection.
    page.locator('#titleFont').select_option('masafont_regular')
    page.locator('#fontSearch').fill('');page.locator('#lyricFont').select_option('iansui')
    page.locator('#titleSize').fill('420');page.locator('#lyricSize').fill('160')
    expect(page.locator('#templateContinue')).to_be_disabled()
    page.locator('#saveTemplate').click()
    expect(page.locator('#templateContinue')).to_be_enabled(timeout=60000)
    expect(page.locator('#templateFrames img')).to_have_count(5)
    page.wait_for_function("[...document.querySelectorAll('#templateFrames img,.font-sample')].every(i=>i.complete&&i.naturalWidth>0)")
    page.screenshot(path='/tmp/music-template-desktop.png',full_page=True)
    page.locator('#templateFrame').click();expect(page.locator('#frameViewer')).to_be_visible();page.locator('#closeFrame').click()
    page.reload();page.wait_for_load_state('networkidle')
    expect(page.locator('#titleFont')).to_have_value('masafont_regular')
    expect(page.locator('#lyricFont')).to_have_value('iansui')
    expect(page.locator('#titleSize')).to_have_value('420');expect(page.locator('#lyricSize')).to_have_value('160')
    expect(page.locator('#templateFrames img')).to_have_count(5)
    expect(page.locator('#lyricEditor')).to_have_value('[status:approved]\n[00:00.00]於是我們繼續\n[00:01.00]繁體書法歌詞\n')
    page.set_viewport_size({'width':390,'height':844});page.screenshot(path='/tmp/music-template-mobile.png',full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    page.locator('#templateContinue').click();page.locator('#makeCover').click()
    expect(page.locator('#coverImage')).to_be_visible(timeout=30000)
    page.wait_for_function("document.getElementById('coverImage').complete&&document.getElementById('coverImage').naturalWidth===2560")
    assert not errors, errors
    assert all(url.startswith(base+'/') for url in requests),requests
    assert not any('lyrics/prepare' in url for url in requests)
    print('PASS: upload, approve, independent font/size, five frames, zoom, refresh, mobile, local cover; all requests local',flush=True)
    browser.close()
