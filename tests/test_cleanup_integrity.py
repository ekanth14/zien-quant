import tempfile
import unittest
from pathlib import Path
from marketscope.candle_archive import CandleArchive


class ArchiveIntegrityTests(unittest.TestCase):
    def test_invalid_prices_never_enter_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            archive=CandleArchive(str(Path(directory)/'candles.db'))
            valid=dict(t=1793331900,o=100,h=102,l=99,c=101,v=10,closed=True)
            for change in [dict(c=float('nan')),dict(v=-1),dict(h=98),dict(o=True),dict(c=None)]:
                self.assertEqual(archive.archive_closed_candles('TCS',5,[{**valid,**change}],source='TEST'),0)
            self.assertEqual(archive.archive_closed_candles('TCS',5,[valid],source='TEST'),1)
