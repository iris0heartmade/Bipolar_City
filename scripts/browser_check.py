"""真实浏览器端到端检查；独立服务、临时存档，不改用户城市。

运行 python -m scripts.browser_check（需已安装 playwright 与 Chromium）。
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

from backend.config import ROOT
from backend.core.errors import GameError
from backend.model.world import World
from backend.systems.placement import validate_placement


def read_state(url):
    with urlopen(url + "/api/state", timeout=3) as response:
        return json.load(response)


def choose_position(state, definition, rotation):
    world=World.from_dict(state["world"])
    for y in range(len(world.terrain)):
        for x in range(len(world.terrain[0])):
            try:
                validate_placement(world,definition,x,y,rotation)
                return x,y
            except GameError:
                continue
    raise AssertionError("没有合法地块")


def wait_state(url, predicate, timeout=10):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        state=read_state(url)
        if predicate(state):return state
        time.sleep(.08)
    raise AssertionError("后台状态未在时限内达到预期")


def click_cell(page,x,y):
    p=page.evaluate("([x,y])=>window.bipoleView.screenAt(x,y)",[x,y])
    page.mouse.move(p["x"],p["y"])
    page.mouse.click(p["x"],p["y"])


def run():
    artifacts=ROOT/"artifacts";artifacts.mkdir(exist_ok=True)
    results=[];errors=[]
    with socket.socket() as socket_handle:
        socket_handle.bind(("127.0.0.1",0));port=socket_handle.getsockname()[1]
    url="http://127.0.0.1:%d" % port
    with tempfile.TemporaryDirectory(prefix="bipole-qa-") as saves:
        env={**os.environ,"BIPOLE_PORT":str(port),"BIPOLE_SAVE_DIR":saves,"PYTHONIOENCODING":"utf-8"}
        with (artifacts/"browser-server.log").open("w",encoding="utf-8") as log:
            process=subprocess.Popen([sys.executable,"-m","backend.app"],cwd=ROOT,env=env,stdout=log,stderr=log,
                                     creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
            try:
                deadline=time.monotonic()+15
                while True:
                    try:initial=read_state(url);break
                    except OSError:
                        if process.poll() is not None or time.monotonic()>deadline:raise
                        time.sleep(.1)
                with sync_playwright() as playwright:
                    browser=playwright.chromium.launch(headless=True)
                    page=browser.new_page(viewport={"width":1440,"height":1000},device_scale_factor=1)
                    page.on("pageerror",lambda error:errors.append(str(error)))
                    page.goto(url);page.wait_for_function("window.bipoleView && document.getElementById('cash').textContent==='48,000'")
                    page.screenshot(path=str(artifacts/"desktop.png"),full_page=True)
                    assert page.get_by_role("button",name="建造工人公寓").is_visible()
                    assert not page.evaluate("document.documentElement.scrollWidth>innerWidth")
                    results.append("1440×1000 桌面页面加载，已有城镇可见，无横向溢出")

                    page.get_by_role("button",name="建造工人公寓").click();page.keyboard.press("r")
                    click_cell(page,23,7)
                    page.wait_for_function("document.getElementById('toast').textContent.includes('河流')")
                    assert read_state(url)["world"]==initial["world"]
                    results.append("真实鼠标点击河流被拒绝，现金/人口/建筑/时间均未改变")

                    x,y=choose_position(initial,"apartment",1);click_cell(page,x,y)
                    blueprint=wait_state(url,lambda s:s["summary"]["blueprints"]==1)
                    assert blueprint["world"]["cash"]==initial["world"]["cash"]
                    assert blueprint["world"]["buildings"][-1]["rotation"]==1
                    page.screenshot(path=str(artifacts/"blueprint.png"),full_page=True)
                    page.get_by_role("button",name="确认施工 →").click()
                    committed=wait_state(url,lambda s:s["summary"]["construction"]==1)
                    assert committed["world"]["cash"]==initial["world"]["cash"]-780000
                    page.get_by_role("button",name="⚒",exact=False).click()
                    assert page.locator('#queue-list').inner_text().find('工人公寓')>=0
                    page.get_by_role("button",name="关闭施工队列").click()
                    results.append("键盘 R 旋转非正方形公寓；鼠标放免费蓝图；确认仅扣 7,800；施工队列显示真实项目")

                    page.get_by_role("button",name="2×",exact=True).click()
                    page.get_by_role("button",name="继续模拟").click()
                    running=wait_state(url,lambda s:s["world"]["day"]>=1)
                    page.get_by_role("button",name="暂停模拟").click()
                    stopped=wait_state(url,lambda s:s["world"]["paused"])
                    time.sleep(.8)
                    assert read_state(url)["world"]["day"]==stopped["world"]["day"]
                    assert stopped["world"]["buildings"][-1]["progress"]>=1
                    page.get_by_role("button",name="推进一个月 →").click()
                    complete=wait_state(url,lambda s:s["world"]["day"]>=stopped["world"]["day"]+30)
                    assert complete["world"]["buildings"][-1]["status"]=="completed"
                    assert complete["summary"]["capacity"]==336 and complete["world"]["population"]==182
                    assert complete["world"]["cash"]==initial["world"]["cash"]-780000+complete["world"]["history"][-1]["net"]
                    results.append("2× 自动日推进与施工运行；暂停后停止；手动月结算竣工，容量 280→336，人口 168→182，财政守恒")

                    page.get_by_role("button",name="财政账本",exact=True).click()
                    assert '-7,800' in page.locator('#ledger-list').inner_text()
                    page.get_by_role("button",name="城市概况",exact=True).click()
                    click_cell(page,x,y)
                    assert page.locator('#selected-name').inner_text()=='工人公寓'
                    assert '90°' in page.locator('#selected-stats').inner_text()
                    page.get_by_role("button",name="关闭建筑详情").click()
                    page.get_by_role("button",name="▦ 格子").click();assert page.locator('#grid-btn').get_attribute('aria-pressed')=='true'
                    page.get_by_role("button",name="⌂ 住房").click();assert page.locator('#layer-btn').get_attribute('aria-pressed')=='true'
                    page.get_by_role("button",name="放大地图").click();assert page.locator('#zoom-label').inner_text()=='115%'
                    canvas=page.locator('#city-canvas').bounding_box();before=page.evaluate('window.bipoleView.screenAt(0,0)')
                    page.mouse.move(canvas['x']+canvas['width']/2,canvas['y']+80);page.mouse.down();page.mouse.move(canvas['x']+canvas['width']/2+70,canvas['y']+100,steps=5);page.mouse.up()
                    after=page.evaluate('window.bipoleView.screenAt(0,0)');assert abs(after['x']-before['x']-70)<1
                    page.get_by_role("button",name="重置地图视角").click()
                    results.append("真实账本/建筑详情/住房图层/网格/缩放/拖动可用")

                    page.get_by_role("button",name="▣ 保存城市").click()
                    wait_state(url,lambda s:s["save_exists"])
                    page.get_by_role("button",name="推进一个月 →").click();wait_state(url,lambda s:s["world"]["day"]>=complete["world"]["day"]+30)
                    page.once("dialog",lambda dialog:dialog.accept())
                    page.get_by_role("button",name="读取",exact=True).click()
                    recovered=wait_state(url,lambda s:s["world"]["day"]==complete["world"]["day"])
                    assert recovered["world"]==complete["world"]
                    results.append("浏览器保存→推进→确认读取，整个世界状态逐字段一致")

                    page.get_by_role("button",name="操作指南").click();assert page.locator('#help-dialog').is_visible();page.get_by_role('button',name='开始书写城市故事 →').click()
                    page.get_by_role('button',name='住宅',exact=True).click();assert page.locator('.building-card').count()==2
                    page.get_by_role('button',name='全部',exact=True).click();assert page.locator('.building-card').count()==5
                    page.screenshot(path=str(artifacts/"desktop-played.png"),full_page=True)

                    mobile=browser.new_page(viewport={"width":390,"height":844},device_scale_factor=1,is_mobile=True,has_touch=True)
                    mobile.on('pageerror',lambda error:errors.append(str(error)))
                    mobile.goto(url);mobile.wait_for_function('window.bipoleView')
                    assert not mobile.evaluate('document.documentElement.scrollWidth>innerWidth')
                    mobile.get_by_role('button',name='建造河岸联排住宅').tap()
                    assert mobile.locator('#city-canvas').bounding_box()['y']>=0
                    mx,my=choose_position(recovered,'row_house',0);point=mobile.evaluate('([x,y])=>window.bipoleView.screenAt(x,y)',[mx,my]);mobile.touchscreen.tap(point['x'],point['y'])
                    wait_state(url,lambda s:s['summary']['blueprints']==1)
                    mobile.get_by_role('button',name='取消蓝图',exact=True).tap();canceled=wait_state(url,lambda s:s['summary']['blueprints']==0)
                    assert canceled['world']['cash']==complete['world']['cash']
                    mobile.screenshot(path=str(artifacts/'mobile.png'),full_page=True)
                    results.append('390×844 手机页面无横向溢出；触摸放置和取消蓝图，资金未变')
                    assert not errors, errors
                    results.append('桌面/手机未捕获到 JavaScript 运行异常')
                    browser.close()
            except Exception:
                (artifacts/'browser-results.json').write_text(json.dumps({'checks':results,'javascript_errors':errors,'status':'failed'},ensure_ascii=False,indent=2),encoding='utf-8')
                raise
            finally:
                process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:process.kill();process.wait()
    report={'checks':results,'javascript_errors':errors}
    (artifacts/'browser-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':run()
