"""Run from project root with PYTHONPATH=. and the project Python SDK."""
import json
from pathlib import Path
import threading
from playwright.sync_api import sync_playwright
from src.control_center.demo import DemoControlCenterService
from src.control_center.http import create_server

root = Path(__file__).parent
service = DemoControlCenterService('predictions-available')
server = create_server(service, port=0)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
result = {'viewports': [], 'errors': []}
try:
 with sync_playwright() as p:
  browser = p.chromium.launch()
  page = browser.new_page(viewport={'width':1440,'height':1000})
  page.on('pageerror', lambda error: result['errors'].append(str(error)))
  page.goto(f'http://127.0.0.1:{server.server_address[1]}')
  page.wait_for_function('app.data !== null')
  result['helpers'] = page.evaluate('''() => ({role:expectedRole({expected_minutes:null}),rate:fpPerMinute({fp_per_minute:null,expected_fp:20,expected_minutes:25}),missing:fpPerMinute({expected_fp:null,expected_minutes:20}),zero:fpPerMinute({expected_fp:0,expected_minutes:20})})''')
  assert result['helpers'] == {'role':'UNKNOWN','rate':.8,'missing':None,'zero':0}, result
  result['team_fp_rules'] = page.evaluate('''() => {
    const players=Array.from({length:10},(_,i)=>({entity_type:'PLAYER',expected_fp:(i+1)*2}));
    const coach={entity_type:'COACH',expected_score:7};
    return {full:rosterFantasyProjection([...players,coach]).value,
      players:rosterFantasyProjection(players).value,
      missing:rosterFantasyProjection([...players.slice(1),{expected_fp:null}]).value,
      zero:rosterFantasyProjection(players.map(p=>({...p,expected_fp:0}))).value,
      empty:rosterFantasyProjection([]).value};
  }''')
  # Sum 110 - half of the bottom four (10) + captain's extra 20 + coach 7.
  assert result['team_fp_rules'] == {'full':127,'players':120,'missing':119,'zero':0,'empty':None}
  assert page.evaluate('''() => {
    const rows=Array.from({length:10},(_,i)=>({expected_fp:i<8?10:null}));
    const result=rosterFantasyProjection(rows);
    return result.partial && result.label.includes('8/10') && result.value===80;
  }''')
  result['recommendation_identity'] = page.evaluate('''() => {
    const data={state:{profile_id:'team2',season_code:'E2026',fantasy_matchday:1,roster_entity_ids:['a'],bank_credits:3,transfers_available:2,player_constraints:{}},dashboard:{prediction:{prediction_fingerprint:'p',market_snapshot_fingerprint:'m'}}};
    const rec={profile_id:'team2',season:'E2026',matchday:1,current_roster:['a'],bank_credits:3,transfers_available:2,constraints:{},snapshot:{prediction_fingerprint:'p',market_fingerprint:'m'}};
    return {same:recommendationMatchesCurrent(rec,data),changedRoster:recommendationMatchesCurrent({...rec,current_roster:['b']},data),changedTeam:recommendationMatchesCurrent({...rec,profile_id:'team1'},data),changedSnapshot:recommendationMatchesCurrent({...rec,snapshot:{...rec.snapshot,prediction_fingerprint:'old'}},data),missingFile:recommendationMatchesCurrent({...rec,restored_without_snapshot:true},data)};
  }''')
  assert result['recommendation_identity'] == {'same':True,'changedRoster':False,'changedTeam':False,'changedSnapshot':False,'missingFile':False}
  page.locator('[data-tab="recommendations"]').click()
  result['changes_only'] = page.evaluate('''() => ({roster:document.querySelectorAll('#recommendation-cards .recommendation-roster').length,changes:document.querySelectorAll('#recommendation-cards .transfer-players').length})''')
  assert result['changes_only']['roster'] == 0 and result['changes_only']['changes'] > 0
  page.evaluate('''() => renderRecommendations({...app.recommendation,optimization_mode:'BUILD_NEW'})''')
  result['new_team_full_roster'] = page.locator('#recommendation-cards .recommendation-roster').count() > 0
  assert result['new_team_full_roster']
  page.locator('[data-tab="builder"]').click()
  # Production market rows do not carry recent form; Builder must merge it from players.
  page.evaluate('''() => {app.data.market=app.data.market.map(row => {const copy={...row};delete copy.last5_fp_average;delete copy.last5_minutes_median;delete copy.last5_games;return copy;});renderTeamBuilder();}''')
  result['builder_recent'] = page.evaluate('''() => marketWithPredictions().some(row => row.last5_fp_average != null && row.last5_minutes_median != null)''')
  assert result['builder_recent']
  result['live_switch_contract'] = page.evaluate('''() => {
    const html=renderSwitch({keep_player:'Outgoing',switch_player:'Incoming',keep_realized_fp:9,new_player:{expected_fp:21,expected_minutes:27,p10:10,p50:20,p90:33},old_player:{expected_fp:14,expected_minutes:18},expected_fantasy_gain:3});
    return html.includes('Incoming') && html.includes('Outgoing') && html.includes('27.0') && html.includes('21.0') && html.includes('18.0');
  }''')
  assert result['live_switch_contract']
  result['team_labels'] = page.evaluate('''() => {
    const row=app.data.market.find(row=>row.team_name);
    return (!row || teamLabel(row.team_id)===row.team_name) && playerCell({opponent_team_id:'opaque-id',opponent_team_name:'Opponent Club'},{key:'opponent_team_id'})==='Opponent Club';
  }''')
  assert result['team_labels']
  page.locator('[data-tab="team"]').click()
  result['current_team_recent'] = 'L5 FP' in page.locator('#team-groups').inner_text() and 'L5 MIN' in page.locator('#team-groups').inner_text()
  assert result['current_team_recent']
  page.evaluate('''() => {const p=app.data.players[0],a=app.data.players[1];renderCurrentTeamStrategy({season:'E2026',matchday:1,bank_credits:3,current_team:{roster_size:1,missing_positions:{},slots_to_fill:10},actions:[{...p,recommendation:'POSSIBLE_REPLACEMENT',alternative:a,locked:false}],fill_candidates:{},strong_position:'GUARD',weak_position:'CENTER'});}''')
  result['strategy_recent'] = 'L5 FP' in page.locator('#current-team-strategy').inner_text() and 'L5 MIN' in page.locator('#current-team-strategy').inner_text()
  assert result['strategy_recent']
  page.evaluate("showPlayerDetail(app.data.players[0].player_id)")
  page.wait_for_selector('#player-detail .last-five-games span')
  result['player_detail_recent_games'] = page.locator('#player-detail .last-five-games span').count()
  assert result['player_detail_recent_games'] == 5
  page.locator('#player-detail #detail-close').click()
  for width in (1440,1100,768,390):
   page.set_viewport_size({'width':width,'height':1000})
   page.locator('[data-tab="recommendations"]').click()
   bounds = page.evaluate('''() => ({width:innerWidth,document:document.documentElement.scrollWidth,overflow:[...document.querySelectorAll('.recommendation-player')].filter(e => {let a=e.getBoundingClientRect();return [...e.children].some(c=>c.getBoundingClientRect().right>a.right+1)}).length})''')
   result['viewports'].append(bounds)
   assert bounds['document'] <= width and bounds['overflow']==0,bounds
   if width==768: page.screenshot(path=str(root/'optimization-after-768.png'),full_page=True)
  # Deliberately delay an old bootstrap; changing slots must reject it.
  page.set_viewport_size({'width':1440,'height':1000})
  page.locator('[data-tab="team"]').click()
  page.evaluate('''() => {window.realApi=api;window.oldBootstrap=null;api=(path,options)=>path==='/api/bootstrap'?new Promise(resolve=>window.oldBootstrap=resolve):path==='/api/team/context'?Promise.resolve({state:{...app.data.state,profile_id:'demo:team-2',roster_entity_ids:[]}}):realApi(path,options);window.oldData=structuredClone(app.data);load();}''')
  page.locator('#tab-team [data-team-slot="2"]').click()
  page.wait_for_function("app.data.state.profile_id === 'demo:team-2'")
  page.evaluate('oldBootstrap(oldData)')
  page.wait_for_timeout(50)
  result['stale_bootstrap_ignored']=page.evaluate("app.activeTeamSlot === 2 && app.data.state.profile_id === 'demo:team-2'")
  assert result['stale_bootstrap_ignored']
  # Restore mock routing for actual keyboard events without a production backend.
  page.locator('#tab-team [data-team-slot="2"]').focus()
  page.keyboard.press('ArrowLeft')
  page.wait_for_function('app.activeTeamSlot === 1 && !app.busy.size')
  result['keyboard_team_switch']=True
  browser.close()
finally:
 server.shutdown();server.server_close();thread.join(timeout=3)
 (root/'browser-after.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
