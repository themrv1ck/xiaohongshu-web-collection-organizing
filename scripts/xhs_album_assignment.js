// Generated with an immutable payload and the shared visible-context checks.
(() => {
PAYLOAD_AND_CORE
  const startedAt = performance.now();
  const elapsedMs = () => performance.now() - startedAt;
  // Observed public Collect component: 500 ms, leading=true, trailing=false.
  // Respect its duplicate-click gate; do not probe or alter the page runtime.
  // Source hash and observation boundary: references/visible-collection-entry.md.
  const MIN_COLLECT_TOGGLE_GAP_MS = 500;
  let collectNotBeforeMs = 0;
  const observations = [], clickLog = [];
  let lastObservation = '';
  const assertNote = () => {
    xhsUiAssertContext(payload);
    if (xhsUiIdFromPath(window.location.href, 'note') !== payload.note_id) {
      throw new Error('note page binding mismatch');
    }
  };
  const collectState = () => {
    const control = document.querySelector('#note-page-collect-board-guide');
    const icon = control && control.querySelector('use');
    const href = icon && (icon.getAttribute('href') || icon.getAttribute('xlink:href')) || '';
    if (!xhsUiVisible(control) || (!href.endsWith('#collect') && !href.endsWith('#collected'))) {
      throw new Error('visible collect state is unknown');
    }
    const blockedAncestor = control.closest('[disabled],[aria-disabled="true"],[aria-busy="true"],[inert]');
    const block = blockedAncestor ? 'disabled_or_busy' :
      getComputedStyle(control).pointerEvents === 'none' ? 'pointer_events_none' : '';
    const collected = href.endsWith('#collected');
    const signature = String(collected) + ':' + block;
    if (signature !== lastObservation) {
      observations.push({elapsed_ms:elapsedMs(),collected,actionable:!block,blocked_by:block});
      if (observations.length > 20) observations.shift();
      lastObservation = signature;
    }
    return {control, collected, actionable:!block};
  };
  assertNote();
  const initial = collectState();
  if (initial.collected !== payload.initial_collected) throw new Error('visible collect state changed before execution');
  if (initial.collected && payload.allow_recollect !== true) throw new Error('explicit recollect consent is required');
  if (!initial.actionable) throw new Error('visible collect control is not actionable; no write attempted');

  const runId = 'xhs_skill_' + Date.now() + '_' + Math.floor(Math.random() * 1000000);
  const state = document.createElement('div');
  state.id = 'xhs-skill-run-state-' + runId;
  state.hidden = true;
  state.dataset.xhsSkillState = 'running';
  state.textContent = JSON.stringify({done:false,events:[]});
  document.documentElement.appendChild(state);
  const events = [];
  let phase = initial.collected ? 'await_uncollected' : 'await_collected';
  let writeAttempted = false, finished = false, driving = false;
  let observer = null, timer = null, deadlineTimer = null;
  let panelSignature = '', panelStableTicks = 0;
  const stop = () => {
    finished = true;
    if (observer) observer.disconnect();
    if (timer !== null) clearInterval(timer);
    if (deadlineTimer !== null) clearTimeout(deadlineTimer);
  };
  const publish = value => {
    state.dataset.xhsSkillState = value.preview_only ? 'preview' : value.ok ? 'ok' : value.done ? 'error' : 'running';
    state.textContent = JSON.stringify({...value, phase, events, write_attempted:writeAttempted,
      diagnostics:{elapsed_ms:elapsedMs(),observations,clicks:clickLog,
        min_collect_toggle_gap_ms:MIN_COLLECT_TOGGLE_GAP_MS,collect_not_before_ms:collectNotBeforeMs}});
  };
  const fail = error => {
    stop();
    const message = error && error.message ? error.message : String(error);
    publish({done:true,ok:false,error:(writeAttempted
      ? 'HIGH_RISK_STATE_UNCERTAIN: collection or album write was attempted; phase=' + phase + '; '
      : '') + message});
  };
  const clickCollect = expected => {
    assertNote();
    const live = collectState();
    if (live.collected !== expected) throw new Error('collect state changed before toggle');
    if (!live.actionable) throw new Error('visible collect control is not actionable');
    writeAttempted = true;
    clickLog.push({action:expected?'uncollect':'collect',elapsed_ms:elapsedMs()});
    events.push(expected ? 'ui:uncollect_clicked' : 'ui:collect_clicked');
    publish({done:false});
    xhsUiClick(live.control);
    // Measure after dispatch returns, so the second event cannot precede the
    // platform handler's own 500 ms timer even if the first dispatch took time.
    collectNotBeforeMs = elapsedMs() + MIN_COLLECT_TOGGLE_GAP_MS;
  };
  const drive = () => {
    if (finished || driving) return;
    driving = true;
    try {
      assertNote();
      const live = collectState();
      if (phase === 'await_uncollected') {
        if (live.collected) return;
        events.push('ui:uncollected_observed');
        phase = 'await_recollect_ready';
        publish({done:false});
        return; // A changed icon alone does not prove the control can be clicked again.
      }
      if (phase === 'await_recollect_ready') {
        if (live.collected) throw new Error('uncollected state reverted before recollect');
        if (!live.actionable) return;
        if (elapsedMs() < collectNotBeforeMs) return;
        events.push('ui:recollect_control_ready');
        phase = 'await_collected'; // Set before click so observers cannot click twice.
        clickCollect(false);
        return;
      }
      if (phase === 'await_collected') {
        if (!live.collected) return;
        events.push('ui:collected_observed');
        phase = 'await_join';
      }
      if (!live.collected) throw new Error('collection state was lost during album assignment');
      if (phase === 'await_join') {
        const joins = Array.from(document.querySelectorAll('.tooltip-container .right-area,.right-area'))
          .filter(e => xhsUiText(e.innerText) === '加入专辑' && xhsUiVisible(e));
        if (joins.length > 1) throw new Error('visible join control is not unique');
        if (!joins.length) return;
        phase = 'await_board';
        events.push('ui:join_album_visible','ui:join_album_clicked');
        publish({done:false});
        xhsUiClick(joins[0]);
      }
      if (phase === 'await_board') {
        const boards = Array.from(document.querySelectorAll('.board-list .board-item'))
          .filter(e => xhsUiText(e.innerText) === payload.target_board && xhsUiVisible(e));
        if (boards.length > 1) throw new Error('target board visible match count exceeds 1');
        if (boards.length === 1) {
          boards[0].scrollIntoView({block:'nearest'});
          if (payload.preview_only === true) {
            phase = 'picker_verified';
            events.push('board:FOUND:' + payload.target_board,'ui:stopped_before_album_selection');
            stop();
            publish({done:true,ok:false,preview_only:true,
              preview:{id:payload.note_id,target_board:payload.target_board,
                recollected:initial.collected,album_write_attempted:false}});
            return;
          }
          phase = 'await_confirmation';
          events.push('board:FOUND:' + payload.target_board,'ui:board_clicked');
          publish({done:false});
          xhsUiClick(boards[0]);
        } else {
          const root = document.querySelector('.board-list-container');
          if (root && xhsUiVisible(root)) {
            const scrollables = [root, ...root.querySelectorAll('*')].filter(e =>
              xhsUiVisible(e) && /^(auto|scroll)$/.test(getComputedStyle(e).overflowY)
              && e.scrollHeight > e.clientHeight);
            if (scrollables.length > 1) throw new Error('album selector scroll container is ambiguous');
            const panel = scrollables[0] || root;
            const count = document.querySelectorAll('.board-list .board-item').length;
            const signature = [panel.scrollTop,panel.scrollHeight,panel.clientHeight,count].join(':');
            panelStableTicks = signature === panelSignature ? panelStableTicks + 1 : 0;
            panelSignature = signature;
            if (panel.scrollTop + panel.clientHeight < panel.scrollHeight - 1) {
              panel.scrollTop = Math.min(panel.scrollHeight, panel.scrollTop + Math.max(1,panel.clientHeight-24));
            } else if (count > 0 && panelStableTicks >= 10) {
              throw new Error('target board is absent from the visible album selector');
            }
          }
        }
      }
      if (phase === 'await_confirmation') {
        const success = Array.from(document.querySelectorAll('.message-container,.msg-container,.left-area'))
          .find(e => xhsUiText(e.innerText) === '已加入' + payload.target_board && xhsUiVisible(e));
        if (success) {
          events.push('ui:join_confirmed');
          phase = 'visible_confirmation';
          stop();
          publish({done:true,ok:true,result:{id:payload.note_id,target_board:payload.target_board,
            events, recollected:initial.collected, visible_confirmation:xhsUiText(success.innerText)}});
        }
      }
    } catch(error) { fail(error); }
    finally { driving = false; }
  };
  observer = new MutationObserver(drive);
  observer.observe(document.body,{subtree:true,childList:true,characterData:true,attributes:true});
  timer = setInterval(drive,100);
  deadlineTimer = setTimeout(() => {
    if (!finished) fail(new Error('visible collect-to-album flow timed out'));
  },payload.timeout_ms);
  try { clickCollect(initial.collected); } catch(error) { fail(error); }
  return runId;
})()
