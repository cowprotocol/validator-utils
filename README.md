# Validator

Utilities for computing validity of settled auctions. Currently only computes disregarded utility (missed surplus) for all settled orders in an auction.

## Install:

```bash
python -m venv venv
. venv/bin/activate
pip install -r requirements.txt
```

## Usage:

A number of environment variables need to be set, set them in `validator/env` and rename the file to `validator/.env`. 

Then for computing disregarded utility of a solution:

```bash
python -m validator.du auction_id
```

See 

```bash
python -m validator.du -h
```

for all options.

## Output:

Example:

```bash
Solver   :      ParaSwap
Solution :      0x59437bd098963754d23274841e2d255e72413ce8b6960c38d9367da2049af330
+------------------+-------------+---------------+-------------+-------------+----------+--------+
|      Order       |   Surplus   | Surplus (ETH) | Surplus (%) |      DU     | DU (ETH) | DU (%) |
+------------------+-------------+---------------+-------------+-------------+----------+--------+
| 0x35917..2cdae59 | 0.1644 WETH |    0.164416   |    5.0000   | 0.0020 WETH | 0.002010 | 0.0611 |
| 0x277ee..2cdae49 | 0.0929 WETH |    0.092943   |    5.0004   | 0.0000 WETH | 0.000000 | 0.0000 |
| 0x1dcfb..2cdae4b | 0.1090 WETH |    0.108969   |    4.7755   | 0.0000 WETH | 0.000000 | 0.0000 |
| 0x6e813..2cdae55 | 0.1175 WETH |    0.117458   |    5.0004   | 0.0000 WETH | 0.000000 | 0.0000 |
+------------------+-------------+---------------+-------------+-------------+----------+--------+
```

Each row shows the disregarded utility of an order. The "Surplus" columns show the surplus the order got in the submitted solution, and "DU" the additional surplus the order *should* have gotten.


## Current limitations / TODO list:

* No access to 0x liquidity orders existing in the instance, which may underestimate disregarded utility.
* No access to the order cost estimations, which may overestimate disregarded utility.
* Limited to the public liquidity sourced by lpbook and handled by quasimodo: currently only uniswap V2, sushiswap and curve.
* Need to constrain direction in which AMM was used when resolving.
