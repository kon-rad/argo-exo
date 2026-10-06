import threading
import time

from exo_deck.hookq import HookQueue


def test_start_then_stop_run_in_order_and_do_not_overlap():
    events, gate = [], threading.Event()

    def runner(name, *args):
        events.append(("begin", name))
        if name == "talk-start":
            gate.wait(2)
        events.append(("end", name))

    q = HookQueue(runner)
    q.put("talk-start")
    q.put("talk-stop")
    time.sleep(0.1)
    assert events == [("begin", "talk-start")]   # stop has not started while start runs
    gate.set()
    assert q.join(2)
    assert events == [("begin", "talk-start"), ("end", "talk-start"),
                      ("begin", "talk-stop"), ("end", "talk-stop")]


def test_second_start_while_one_queued_is_dropped():
    ran, gate = [], threading.Event()

    def runner(name, *args):
        ran.append(name)
        gate.wait(2)

    q = HookQueue(runner)
    assert q.put("talk-start")
    time.sleep(0.05)                      # first start now running
    assert q.put("talk-start")            # queued behind it
    assert not q.put("talk-start")        # duplicate while queued: dropped
    gate.set()
    assert q.join(2)
    assert ran == ["talk-start", "talk-start"]


def test_runner_exception_does_not_kill_worker():
    ran = []

    def runner(name, *args):
        ran.append(name)
        if name == "talk-cancel":
            raise OSError("boom")

    q = HookQueue(runner)
    q.put("talk-cancel")
    q.put("repeat")
    assert q.join(2)
    assert ran == ["talk-cancel", "repeat"]
