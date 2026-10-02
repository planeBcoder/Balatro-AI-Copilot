import json
import os
from pathlib import Path
import threading
import time

import pytest

from core.errors import CopilotError
from core.state_refresh import StateRefresher


def test_no_game(tmp_path):
    with pytest.raises(CopilotError, match="未检测"):
        StateRefresher(tmp_path, lambda: set(), lambda _: None, .1).refresh()


def test_legacy_stale_rejected(tmp_path, state_data):
    (tmp_path / "balatro_state.json").write_text(json.dumps(state_data))
    with pytest.raises(CopilotError, match="刷新失败"):
        StateRefresher(tmp_path, lambda: {1}, lambda _: None, .08).refresh()


def test_legacy_fresh_update_accepted(tmp_path, state_data):
    path = tmp_path / "balatro_state.json"
    path.write_text(json.dumps(state_data))
    def trigger(_):
        time.sleep(.015)
        state_data["score"] = "777"
        path.write_text(json.dumps(state_data))
    assert StateRefresher(tmp_path, lambda: {1}, trigger, .3).refresh().score == "777"


def test_bridge_wrong_nonce_not_accepted(tmp_path, state_data):
    (tmp_path / "balatro_copilot_ready.txt").write_text("bridge-v1")
    state_data["request_id"] = "wrong"
    (tmp_path / "balatro_copilot_response.json").write_text(json.dumps(state_data))
    with pytest.raises(CopilotError, match="刷新失败"):
        StateRefresher(tmp_path, lambda: {1}, lambda _: pytest.fail("Must not type F8"), .08).refresh()


def test_bridge_partial_then_correct_nonce(tmp_path, state_data):
    (tmp_path / "balatro_copilot_ready.txt").write_text("bridge-v1")
    def exporter():
        path = tmp_path / "balatro_copilot_request.txt"
        deadline = time.monotonic() + 1
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(.005)
        token = path.read_text().split("|")[0]
        response = tmp_path / "balatro_copilot_response.json"
        response.write_text("{")
        time.sleep(.04)
        state_data["request_id"] = token
        response.write_text(json.dumps(state_data))
    thread = threading.Thread(target=exporter)
    thread.start()
    s = StateRefresher(tmp_path, lambda: {1}, lambda _: pytest.fail("Must not type F8"), .8).refresh()
    thread.join()
    assert s.request_id == (tmp_path / "balatro_copilot_request.txt").read_text().split("|")[0]


def test_busy_request_file_retries_only_read_request(tmp_path,state_data,monkeypatch):
    (tmp_path/'balatro_copilot_ready.txt').write_text('bridge-v1')
    original=os.replace
    tries=[]
    def replace(src,dst):
        tries.append(str(dst))
        if len(tries)<3:raise PermissionError('temporary sharing violation')
        original(src,dst)
        state_data['request_id']=Path(dst).read_text().split('|')[0]
        (tmp_path/'balatro_copilot_response.json').write_text(json.dumps(state_data))
    monkeypatch.setattr('core.state_refresh.os.replace',replace)
    StateRefresher(tmp_path,lambda:{1},lambda _:None,.2).refresh()
    assert len(tries)==3 and not list(tmp_path.glob('*.tmp'))
