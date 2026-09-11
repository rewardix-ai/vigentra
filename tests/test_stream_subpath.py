"""What a playlist may ask the stream proxy to fetch on its behalf.

Every URI inside a brokered playlist comes back through this service, so the
resolver sits between a hostile string and an outbound request. It has to be
permissive enough to serve a real playlist and strict enough not to become an
open forward proxy, and the boundary between those is narrower than it looks:
refusing every path that starts with a slash sounds safe and silently breaks
AES-128 playback, because the key URI is conventionally host-absolute.
"""
from __future__ import annotations

import pytest

MANIFEST = "https://cctv.corp8.cloud/cam01/index.m3u8"


@pytest.fixture(scope="module")
def broker():
    """The broker module, imported after conftest has placed central-api on
    the path. A module-level import fails at collection: the path is set by a
    session fixture, which has not run yet when the file is read."""
    from app.services import video_broker

    return video_broker


def test_relative_segment_resolves_beside_the_manifest(broker):
    assert broker._resolve_hls_target(MANIFEST, "seg00000.ts") == (
        "https://cctv.corp8.cloud/cam01/seg00000.ts"
    )


def test_host_absolute_key_resolves_against_the_same_origin(broker):
    """The case that broke the live wall.

    The grid's playlists carry `#EXT-X-KEY ... URI="/enc.key"`. Refusing it
    delivered every segment and no key, so the player had ciphertext it could
    not decrypt and stopped with nothing to report.
    """
    assert broker._resolve_hls_target(MANIFEST, "/enc.key") == (
        "https://cctv.corp8.cloud/enc.key"
    )


def test_no_sub_path_is_the_manifest_itself(broker):
    assert broker._resolve_hls_target(MANIFEST, None) == MANIFEST


@pytest.mark.parametrize(
    "hostile",
    [
        "https://evil.example/steal.ts",       # its own scheme and host
        "//evil.example/steal.ts",             # protocol-relative, changes host
        "../../etc/passwd",                    # upward traversal
        "seg/../../../secret.ts",              # traversal buried mid-path
    ],
)
def test_the_proxy_refuses_to_fetch_elsewhere(broker, hostile):
    """Allowing one leading slash must not have opened a forward proxy."""
    with pytest.raises(broker.HTTPExceptionLike):
        broker._resolve_hls_target(MANIFEST, hostile)


@pytest.mark.parametrize("other_camera", ["/cam07/index.m3u8", "/cam07/seg00001.ts", "../cam07/index.m3u8"])
def test_a_session_cannot_read_another_cameras_stream(broker, other_camera):
    """Every grid camera shares one host; the host check alone let a cam01
    session stream cam07 through the signed-in client."""
    with pytest.raises(broker.HTTPExceptionLike):
        broker._resolve_hls_target(MANIFEST, other_camera)
