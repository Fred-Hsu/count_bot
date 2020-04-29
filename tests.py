import unittest
import asyncio
import pandas as pd
from unittest.mock import MagicMock
from count_bot import _count
from count_bot import *
from discord import context_managers


def mock_maker_df():
    column_names = [COL_USER_ID, COL_ITEM, COL_VARIANT, COL_COUNT]
    return pd.DataFrame(data=[[123, 'visor', 'verkstan', 25]], columns=column_names)


class TestBot(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.get_event_loop()

    def tearDown(self) -> None:
        self.loop.close()

    def test_count(self):

        ctx = MagicMock()
        ctx.message.author.id = 123

        USER_ROLE_HUMAN_TO_INVENTORY_DF_MAP['makers'] = mock_maker_df()

        result = self.loop.run_until_complete(_count(ctx, 25, trial_run_only=True))
        self.assertEqual(result, (25, 'visor', 'verkstan'))


if __name__ == '__main__':
    unittest.main()
