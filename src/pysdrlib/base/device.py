class Device:
    NAME = "pysdrlib Device"
    CAN_TRANSMIT = False
    def info(self):
        raise NotImplementedError()

    def set_sample_rate(self, Fs):
        """Set sample rate"""
    def get_sample_rate(self):
        """Get sample rate"""
    sample_rate = Fs = property(get_sample_rate, set_sample_rate)

    def set_freq(self, freq):
        """Set center frequency"""
    def get_freq(self):
        """Get center frequency"""
    freq = property(get_freq, set_freq)

    def read_samples(self, samples: int):
        """Read <samples> samples"""
