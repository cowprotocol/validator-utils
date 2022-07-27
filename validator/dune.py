import logging
from duneapi.api import DuneAPI
from duneapi.types import DuneQuery, Network
import asyncio
from .util import traced

logger = logging.getLogger(__name__)


def hex_to_dune(hash):
    return '\\' + hash[1:]


def dune_to_hex(dune_hash):
    return '0' + dune_hash[1:]


@traced(logger, 'Getting gas price from block number through Dune.')
async def get_gas_price_from_block_number(block_number):
    raw_sql = f"""
        SELECT (PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY gas_price)) AS median_gas_price_wei
        FROM ethereum.transactions
        WHERE block_number = {block_number}
        """
    query = DuneQuery.from_environment(
        raw_sql=raw_sql,
        network=Network.MAINNET,
    )

    dune_connection = DuneAPI.new_from_environment()
    data = await asyncio.to_thread(
        dune_connection.fetch,
        query,
    )
    return data[0]['median_gas_price_wei']


@traced(logger, 'Getting block number from txhash through Dune.')
async def get_block_number_from_txhash(txhash):
    dune_txhash = hex_to_dune(txhash)
    raw_sql = f"""
        select block_number from ethereum.transactions where hash = '{dune_txhash}'
    """
    query = DuneQuery.from_environment(
        raw_sql=raw_sql,
        network=Network.MAINNET,
    )
    dune_connection = DuneAPI.new_from_environment()
    data = await asyncio.to_thread(
        dune_connection.fetch,
        query,
    )
    return data[0]['block_number']


@traced(logger, 'Getting touched public pools through Dune.')
async def get_touched_lps(txhash, lps):
    dune_txhash = hex_to_dune(txhash)
    lp_ids = ",".join(["'" + hex_to_dune(lp['address']) + "'" for lp in lps])
    raw_sql = f"""
        select contract_address from ethereum.logs where tx_hash= '{dune_txhash}' and 
        contract_address in ({lp_ids})
    """
    query = DuneQuery.from_environment(
        raw_sql=raw_sql,
        network=Network.MAINNET,
    )
    dune_connection = DuneAPI.new_from_environment()
    data = await asyncio.to_thread(
        dune_connection.fetch,
        query,
    )
    touched_pools = [dune_to_hex(r['contract_address']) for r in data]
    return touched_pools

