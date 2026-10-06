RPC = {"ethereum": "eth.nownodes.io", "base": "base.nownodes.io",
       "arbitrum": "arbitrum.nownodes.io", "polygon": "matic.nownodes.io"}
BLOCKBOOK = {"ethereum": "eth-blockbook.nownodes.io", "base": "base-blockbook.nownodes.io",
             "arbitrum": "arb-blockbook.nownodes.io", "polygon": "maticbook.nownodes.io"}
WSS = {chain: f"wss://{host}/wss/" for chain, host in BLOCKBOOK.items()}
CHAIN_IDS = {"ethereum": 1, "base": 8453, "arbitrum": 42161, "polygon": 137}
