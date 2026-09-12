from .frame_source import Frame, FrameSource
from .file_source import FileSource
from .rtsp_source import RTSPSource
from .hls_source import HLSSource


def open_source(uri: str, camera_id: str = "cam", **kw) -> FrameSource:
    """Factory: pick a source implementation from the URI scheme."""
    low = uri.lower()
    if low.startswith("rtsp://") or low.startswith("rtsps://"):
        return RTSPSource(uri, camera_id, **kw)
    if low.endswith(".m3u8") or "/hls/" in low:
        return HLSSource(uri, camera_id, **kw)
    return FileSource(uri, camera_id, **kw)


__all__ = ["Frame", "FrameSource", "FileSource", "RTSPSource", "HLSSource", "open_source"]
