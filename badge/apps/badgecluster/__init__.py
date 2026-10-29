"""BadgeCluster foreground app with cooperative verified Azure HTTPS."""
import os
import sys
import random
import time

APP_DIR = "/system/apps/badgecluster"
os.chdir(APP_DIR)
sys.path.insert(0, APP_DIR)
from cluster_core import Worker, Contributions
from cluster_session import Session
from cluster_transport import create_transport
from cluster_identity import load_node_id

# Independent per-launch PRNG stream; never derive identity from hardware IDs.
def random_bytes(count):
    if hasattr(os, "urandom"):
        return os.urandom(count)
    if sys.platform == "rp2":
        raise RuntimeError("Device randomness is required")
    print("BadgeCluster: simulator-only pseudorandom bytes; networking disabled.")
    return bytes(random.getrandbits(8) for _ in range(count))


random.seed(int.from_bytes(random_bytes(16), "big"))
worker = Worker(time.ticks_ms(), time.ticks_add, time.ticks_diff,
                lambda: random.getrandbits(24) / 16777216, time.ticks_ms)
node_id = load_node_id(random_bytes) if sys.platform == "rp2" else "node-" + random_bytes(16).hex()
client = Contributions(node_id,
                       "session-" + random_bytes(16).hex(), time.ticks_ms(),
                       time.ticks_add, time.ticks_diff)
session = Session(worker, client, create_transport())
client.next_sync = worker.next_batch
mona = image.load("assets/mona.png")
spheres = [image.load("assets/sphere_%d.png" % i) for i in range(5)]
page = 0
screen.font = font.nope
background = color.rgb(13, 17, 28)
white = color.rgb(240, 246, 252)
muted = color.rgb(160, 174, 195)
accent = color.rgb(122, 229, 211)


def text(value, x, y, pen=white):
    screen.pen = pen
    screen.text(value, x, y)


def centered(value, y, pen=white):
    width, _ = screen.measure_text(value)
    text(value, max(0, (screen.width - width) // 2), y, pen)


def compact(value):
    for threshold, suffix in ((1000000000000, "T"), (1000000000, "B"),
                              (1000000, "M"), (1000, "K")):
        if value >= threshold:
            return "%.1f%s" % (value / threshold, suffix)
    return str(value)


def draw():
    screen.pen = background
    screen.rectangle(screen.clip)
    centered("BADGECLUSTER", 3, accent)
    if page == 0:
        total = client.cluster["samples"] if client.cluster else 0
        milestone = sum(total >= threshold for threshold in (100000, 1000000, 10000000, 100000000))
        screen.blit(spheres[milestone], vec2((screen.width - 40) // 2, 17))
        screen.blit(mona, vec2((screen.width - 56) // 2, 43))
        centered("LEND YOUR POWER!", 86)
        centered(session.status() + (" / TEST" if getattr(session.transport, "test_mode", False) else " / ECO"), 98, muted)
        centered("RIGHT: STATS", screen.height - 10, accent)
    else:
        cluster = client.cluster
        estimate = cluster["piEstimate"] if cluster else None
        text("Pi: " + ("%.5f approx" % estimate if estimate is not None else "--"), 4, 20)
        text("Accepted: " + compact(client.accepted_samples), 4, 34)
        text("Pending: " + compact(worker.samples - client.accepted_samples), 4, 48)
        text("Cluster: " + (compact(cluster["samples"]) if cluster else "--"), 4, 62)
        text("Active: " + (str(cluster["activeNodes"]) if cluster else "--"), 4, 76)
        stale = client.stale(time.ticks_ms())
        status = "NET: " + session.error if session.error else (
            "STALE / " + session.status() if stale else session.status())
        text(status, 4, 94, muted)
        centered("LEFT: MONA", screen.height - 10, accent)


def update():
    global page
    # Return before scheduling any more work when leaving the foreground app.
    if badge.pressed(BUTTON_BACK) or badge.pressed(BUTTON_HOME):
        session.close()
        return False
    if badge.pressed(BUTTON_RIGHT):
        page = 1
    elif badge.pressed(BUTTON_LEFT):
        page = 0
    session.step(time.ticks_ms())
    draw()


def on_exit():
    session.close()


run(update)
on_exit()
