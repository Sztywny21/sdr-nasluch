from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone
from django.utils.timezone import make_aware
from humanize import naturalsize
from sdr.models import *
import base64
import json
import logging
import numpy as np
import os
import re
import sdr.utils.device
import sdr.utils.file

# Jedna próbka na sekundę. Doba schodziła do około 84 tysięcy, tu jest 40 tysięcy.
MAX_SPECTROGRAM_SAMPLES = 40000


class SpectrogramReader:
    def __init__(self):
        self.__logger = logging.getLogger("Spectrogram")
        self.__regex = re.compile("sdr/spectrogram/\\w+/([\\w\\.]+)")

    def get_device(self, name):
        try:
            return Device.objects.get(raw_name=name)
        except ObjectDoesNotExist:
            return Device.objects.create(name=sdr.utils.device.convert_raw_to_name_to_pretty_name(name), raw_name=name)

    def round_down_date(self, date):
        return date.replace(hour=0, minute=0, second=0, microsecond=0)

    def round_up_date(self, date):
        return date.replace(hour=23, minute=59, second=59, microsecond=999999)

    def check_spectrogram_integrity(self, s):
        y_size = np.frombuffer(s.labels, dtype=np.uint64).size
        x_size = (s.end_frequency - s.begin_frequency) // s.step_frequency + 1
        data = np.memmap(s.data_file.path, dtype=np.uint8, mode="r", shape=(y_size, x_size))
        self.__logger.info("total data shape: %s, size: %s" % (str(data.shape), naturalsize(data.size)))

    def sample_count(self, spectrogram):
        raw = spectrogram.labels or b""
        return len(raw) // 8

    def create_spectrogram(self, device, dt, begin_frequency, end_frequency, step_frequency, begin_model_date, end_model_date, source, split):
        middle = (begin_frequency + end_frequency) // 2
        directory = "device_%d/spectrogram" % device.id
        if split:
            stamp = dt.strftime("%H_%M_%S")
            folder = "%s/%s" % (directory, begin_model_date.strftime("%Y-%m-%d"))
            full_dir = os.path.join(settings.MEDIA_ROOT, folder)
            os.makedirs(full_dir, exist_ok=True)
            candidate = "%s_%d_%d.bin" % (stamp, middle, step_frequency)
            extra = 2
            while os.path.exists(os.path.join(full_dir, candidate)):
                candidate = "%s_%d_%d_%d.bin" % (stamp, middle, step_frequency, extra)
                extra += 1
            filename = "%s/%s" % (folder, candidate)
            open(os.path.join(settings.MEDIA_ROOT, filename), "wb").close()
        else:
            filename, _filename_full = sdr.utils.file.get_filename(
                directory,
                begin_model_date,
                "%s_%d_%d.bin" % (begin_model_date.strftime("%H_%M_%S"), middle, step_frequency),
                True,
            )
        self.__logger.info("nowy spektrogram, limit próbek %d" % MAX_SPECTROGRAM_SAMPLES)
        return Spectrogram.objects.create(
            device=device,
            begin_frequency=begin_frequency,
            end_frequency=end_frequency,
            step_frequency=step_frequency,
            begin_model_date=begin_model_date,
            end_model_date=end_model_date,
            begin_real_date=dt,
            end_real_date=dt,
            data_file=filename,
            source=source,
        )

    def append_spectrogram(self, device, dt, begin_frequency, end_frequency, step_frequency, data, source):
        begin_model_date = self.round_down_date(dt)
        device = self.get_device(device)
        latest = (
            Spectrogram.objects.filter(
                device=device,
                begin_frequency=begin_frequency,
                end_frequency=end_frequency,
                step_frequency=step_frequency,
                source=source,
            )
            .order_by("-id")
            .first()
        )
        same_day = latest is not None and timezone.localtime(latest.end_real_date).date() == timezone.localtime(dt).date()
        if latest is not None and same_day and self.sample_count(latest) < MAX_SPECTROGRAM_SAMPLES:
            s = latest
        else:
            # Osobny plik i osobny czas, bo na jedną dobę baza przyjmuje tylko jeden rekord o północy.
            s = self.create_spectrogram(device, dt, begin_frequency, end_frequency, step_frequency, dt, dt, source, True)

        with open(s.data_file.path, "ab") as file:
            file.write(data)
        s.labels += np.array([int(dt.timestamp() * 1000)]).astype(np.uint64).tobytes()
        s.end_real_date = dt
        if s.begin_model_date != begin_model_date:
            s.end_model_date = dt
        s.save()
        # self.check_spectrogram_integrity(s)

    def on_message(self, client, message):
        topic = message.topic
        m = self.__regex.match(topic)
        if m:
            device = m.group(1)
            message = json.loads(message.payload.decode("utf-8"))
            data = base64.b64decode(message["data"])
            source = message["source"]
            frequency = message["frequency"]
            sample_rate = message["sample_rate"]
            begin_frequency = frequency - sample_rate // 2
            end_frequency = frequency + sample_rate // 2
            step_frequency = sample_rate // len(data)
            dt = make_aware(timezone.datetime.fromtimestamp(message["time"] / 1000))
            self.__logger.info(
                f"source: {source}, device: {device}, frequency: {frequency}, sample_rate: {sample_rate}, step: {step_frequency}, datetime: {dt}, data size: {naturalsize(len(data))}"
            )
            self.append_spectrogram(m.group(1), dt, begin_frequency, end_frequency, step_frequency, data, source)
            return True
        return False
