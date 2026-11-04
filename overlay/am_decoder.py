#!/usr/bin/env python3

from gnuradio import audio, blocks, filter, gr
from gnuradio.fft import window
from gnuradio.filter import firdes
import itertools
import signal
import sys


class am_decoder(gr.top_block):
    def __init__(self, input, sample_rate=32000, audio_rate=32000, duration=60, output=blocks.null_sink(gr.sizeof_float * 1)):
        gr.top_block.__init__(self, "am decoder", catch_exceptions=True)
        nyquist = float(sample_rate) / 2.0
        hp_cut = min(40.0, nyquist * 0.02)
        hp_trans = min(20.0, max(8.0, hp_cut * 0.5))
        _blocks = [
            blocks.file_source(gr.sizeof_char * 1, input, False, 0, 0),
            blocks.head(gr.sizeof_char * 1, (int(sample_rate * duration * 2))),
            blocks.add_const_bb(128),
            blocks.interleaved_char_to_complex(False, 1.0),
            blocks.complex_to_mag(1),
            filter.fir_filter_fff(1, firdes.high_pass(1, sample_rate, hp_cut, hp_trans, window.WIN_HAMMING, 6.76)),
            blocks.multiply_const_ff(0.011),
            output,
        ]
        for b1, b2 in itertools.pairwise(_blocks):
            self.connect(b1, b2)


def main(file):
    tb = am_decoder(input=file, output=audio.sink(32000, "", True))

    def sig_handler(sig=None, frame=None):
        tb.stop()
        tb.wait()
        sys.exit(0)

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)
    tb.start()
    tb.wait()


if __name__ == "__main__":
    main(sys.argv[1])
