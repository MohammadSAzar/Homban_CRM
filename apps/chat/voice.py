"""Bounded Ogg/Opus framing validation; no decoding/transcoding or AI.

RFC 7845 container/granule rules and RFC 6716 section 3 packet framing.
Only a single mono/stereo mapping-family-0 stream is accepted.
"""
import struct

from django.utils.translation import gettext_lazy as _
from mutagen import MutagenError
from mutagen.ogg import OggPage
from mutagen.oggopus import OggOpus
from rest_framework.exceptions import ValidationError

VOICE_MIME = 'audio/ogg'
VOICE_LIMIT = 25 * 1024 * 1024
VOICE_SECONDS = 600
SAMPLE_RATE = 48000
PACKET_LIMIT = 65536


def packet_samples(packet):
    """Validate Opus frame lengths and return decoded sample count (no PCM allocation)."""
    if not packet:
        raise ValueError('Empty packet')
    config, code = packet[0] >> 3, packet[0] & 3
    samples = ((480, 960, 1920, 2880)[config % 4] if config < 12 else
               (480, 960)[config % 2] if config < 16 else (120, 240, 480, 960)[config % 4])
    position, end = 1, len(packet)

    def byte():
        nonlocal position
        if position >= end:
            raise ValueError('Truncated packet')
        value = packet[position]
        position += 1
        return value

    def length():
        value = byte()
        return value if value < 252 else value + 4 * byte()

    count, variable = (1 if code == 0 else 2), code == 2
    if code == 3:
        flags = byte()
        count, variable = flags & 63, bool(flags & 128)
        if flags & 64:
            padding = 0
            while True:
                value = byte()
                padding += 254 if value == 255 else value
                if value != 255:
                    break
            end -= padding
    if not count or count * samples > 5760 or end < position:
        raise ValueError('Invalid packet duration/padding')
    if variable:
        sizes = [length() for _ in range(count - 1)]
        sizes.append(end - position - sum(sizes))
    else:
        remaining = end - position
        if remaining % count:
            raise ValueError('Invalid CBR length')
        sizes = [remaining // count]
    if any(size < 0 or size > 1275 for size in sizes):
        raise ValueError('Invalid frame length')
    return samples * count


def voice_duration(upload):
    """Inspect every page/packet, cross-check sample positions, round duration UP.

    CRCs, sequence, EOS and framing are checked; this is not full audio decoding
    or malware scanning. Chained/multiplexed streams and oversized tags are rejected.
    """
    try:
        upload.seek(0)
        sequence = 0
        serial = None
        pending = b''
        packet_index = 0
        samples = 0
        previous_position = 0
        offset = None
        pre_skip = 0
        ended = False
        while not ended:
            start = upload.tell()
            page = OggPage(upload)
            end = upload.tell()
            upload.seek(start)
            raw = upload.read(end - start)  # An Ogg page is at most ~65 KiB.
            if raw != page.write() or raw[5] & ~7:
                raise ValueError('Invalid page checksum/flags')
            if (page.sequence != sequence or page.first != (sequence == 0)
                    or (serial is not None and page.serial != serial)
                    or bool(page.continued) != bool(pending) or not page.packets):
                raise ValueError('Invalid stream sequence')
            serial = page.serial
            sequence += 1
            page_samples = 0
            for index, part in enumerate(page.packets):
                pending += part
                if len(pending) > PACKET_LIMIT:
                    raise ValueError('Packet too large')
                if index == len(page.packets) - 1 and not page.complete:
                    continue
                packet, pending = pending, b''
                if packet_index == 0:
                    if (sequence != 1 or len(page.packets) != 1 or len(packet) != 19
                            or packet[:8] != b'OpusHead' or packet[8] != 1
                            or packet[9] not in (1, 2) or packet[18] != 0):
                        raise ValueError('Unsupported Opus header')
                    pre_skip = struct.unpack_from('<H', packet, 10)[0]
                elif packet_index == 1:
                    if not packet.startswith(b'OpusTags') or index != len(page.packets) - 1:
                        raise ValueError('Invalid comment header')
                else:
                    page_samples += packet_samples(packet)
                packet_index += 1
            samples += page_samples
            if packet_index <= 2:
                if page.position != 0 or page.last:
                    raise ValueError('Invalid header position')
            elif page_samples:
                if offset is None:
                    offset = max(0, page.position - samples)
                if page.last:
                    if not previous_position <= page.position <= offset + samples or pending:
                        raise ValueError('Invalid final position')
                elif page.position != offset + samples:
                    raise ValueError('Inconsistent sample position')
                previous_position = page.position
            elif page.position != -1 or page.last:
                raise ValueError('Missing audio packet')
            if samples > (VOICE_SECONDS * SAMPLE_RATE + pre_skip + 5760):
                raise ValueError('Voice too long')
            ended = bool(page.last)
        if upload.read(1) or packet_index < 3 or offset is None:
            raise ValueError('Trailing/missing audio')
        duration_samples = page.position - offset - pre_skip
        if not 0 < duration_samples <= VOICE_SECONDS * SAMPLE_RATE:
            raise ValueError('Invalid duration')
        # Library also validates Opus header/comment structure. Never persist its float duration.
        upload.seek(0)
        OggOpus(upload)
        return (duration_samples + SAMPLE_RATE - 1) // SAMPLE_RATE
    except (MutagenError, EOFError, OSError, ValueError, IndexError, struct.error):
        raise ValidationError({'attachment': _('فایل صوتی معتبر نیست؛ پیام صوتی باید حداکثر ۱۰ دقیقه باشد.')})
    finally:
        upload.seek(0)
