---
name: exo-wallet
description: Propose a crypto transaction for the wearer (send ETH or a token, or a raw call an agent built). Every proposal goes through the Exo CRE Transaction Guardian, and the wearer approves with the approve key. Use for "send", "pay", "transfer", or when an agent wants to act onchain.
---

# exo-wallet

```
python3 ~/argo-exo/agents/skills/exo-wallet/wallet.py send --token USDC --amount 20 --to mira.eth --summary "lunch" --source voice
python3 ~/argo-exo/agents/skills/exo-wallet/wallet.py raw --to 0x... --value 0 --data 0x... --summary "what it does" --source agent:trader
```

- `--source` is `voice` (the wearer asked) or `agent:<your profile>`.
- `--to` is a 0x address or an address-book label. Amounts are plain decimals (`20`, `0.01`), never more decimal places than the token has.
- Read the printed line to the wearer verbatim; it is the Guardian's explanation. On approval the wearer presses the approve key.
- You never sign, never hold keys, never choose a salt or proposal id, and never retry a refused transaction with changes to get it approved. A refusal is final.
- "Outcome unknown" (exit 1) means the Guardian may still have queued it. Do not retry. Tell the wearer to check the approvals panel.
