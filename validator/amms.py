import asyncio
import json
import logging
import os
from textwrap import indent

import requests
from async_lru import alru_cache

from validator.common import NATIVE_TOKEN, native_token_balance, zero_cost

from .util import freeze_dicts, traced

logger = logging.getLogger(__name__)


def create_amm_from_lp(lp, token_info, gas_price):
    """Note: Also updates token_info with new tokens."""

    token_info = dict(token_info)

    for t in lp['tokens']:
        if t['address'] not in token_info.keys():
            token_info[t['address']] = {
                'alias': t['symbol'],
                'decimals': t['decimals'],
                'external_price': None,
                'normalize_priority': 0,
                'internal_buffer': 0
            }

    cost = native_token_balance(int(lp['gas_stats']['median']) * gas_price)

    return {
        'address': lp['address'],
        'cost': cost,
        'fee': "0.0",
        'kind': 'LPBook',
        'mandatory': False,
        'protocol': lp['protocol'],
        'state': lp['state'],
        'tokens': [{**token_info[t['address']], 'address': t['address']} for t in lp['tokens']]
    }


@alru_cache(maxsize=None)
@traced(logging, "Getting liquidity from LPBook.")
async def get_amms_helper(block_number, token_info, gas_price):
    lpbook_url = os.getenv('LPBOOK_URL')
    base_tokens = os.getenv('BASE_TOKENS')
    token_list = list(set(token_info.keys()) | set(base_tokens.split(',')))

    response = await asyncio.to_thread(
        requests.post,
        lpbook_url + '/lps_trading_tokens_historic',
        params={"block_number": block_number},
        json=token_list
    )

    if response.status_code != 200:
        logging.error("Error getting liquidity from LPBook. Served replied with {response.status_code}: {response.text}")
        raise RuntimeError("Error getting liquidity from LPBook")

    lps = response.json()
    amms = {lp['address']: create_amm_from_lp(lp, token_info, gas_price) for lp in lps}
    return amms


async def get_amms(block_number, token_info, gas_price):
    return await get_amms_helper(block_number, freeze_dicts(token_info), gas_price)


