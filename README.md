# UTEXO Internship Test Assignment — Bitcoin + Lightning (Testnet)

This repository contains my setup and work for the internship test assignment:

- Run a Bitcoin node (testnet/signet)
- Run a Lightning node (CLN/LND)
- Open at least 2 active channels
- Build a tool to inspect liquidity and orchestrate payments
- Document results and experiments

---

## 0) Local Environment

- OS: Windows (PowerShell)
- Docker Desktop: installed and running (virtualization enabled in BIOS)

Project folder:
`C:\Users\pietr\OneDrive\Desktop\utexo-ln-assignment`

---
## 1) Repository structure

- `docker-compose.yml` — infrastructure (bitcoind + 2 CLN nodes)
- `conf/bitcoin.conf` — Bitcoin Core config
- `tool/liquidity_orchestrator.py` — Liquidity Inspector + Payment Orchestrator (CLI)
- `logs/attempts.jsonl` — payment attempt logs (created when using the tool)
- `scripts/` — startup scripts (optional)

---

## 2) Bitcoin node: bitcoind on TESTNET (Docker)

### 2.1 Why Docker
Docker Compose makes the setup reproducible and easy to run on any machine/VPS.
### 2.2 Docker image
- `lncm/bitcoind:v28.0`

### 2.3 Permission fix (important)
bitcoind initially crashed with:
- `Permission denied [/bitcoin/.bitcoin/testnet3/wallets]`
- `Permission denied [/data/testnet3/wallets]`

Cause: container user could not write to the mounted volume.

Fix used for this assignment environment:
- run container as root: `user: "0:0"`
- store data under `/data` in a Docker volume

### 2.4 RPC configuration
Bitcoin Core requires some RPC options to be placed under the `[test]` section.
Also, testnet mode is explicitly enabled (`testnet=1`).

### 2.5 `conf/bitcoin.conf`
ini
testnet=1
server=1
txindex=1

[test]
rpcuser=bitcoin
rpcpassword=bitcoin
rpcallowip=0.0.0.0/0
rpcbind=0.0.0.0
rpcport=18332
zmqpubrawblock=tcp://0.0.0.0:28332
zmqpubrawtx=tcp://0.0.0.0:28333
port=18333

### 2.6 Useful commands (bitcoind)

Start + logs:

```
dockercomposeup-dbitcoind
dockercomposelogs-fbitcoind
```

Blockchain sync status:

```
dockercomposeexecbitcoindbitcoin-cli-testnet-rpcuser=bitcoin-rpcpassword=bitcoingetblockchaininfo
```

Peer/network status:

```
dockercomposeexecbitcoindbitcoin-cli-testnet-rpcuser=bitcoin-rpcpassword=bitcoingetnetworkinfo
```

---

## 3) Lightning nodes: Core Lightning (CLN) on TESTNET (Docker)

### 3.1 CLN image

- `elementsproject/lightningd:stable`

### 3.2 Key fixes applied

- Do not pass `lightningd` twice (image entrypoint already launches it)
- Avoid duplicate listen address by using only:
    - `-bind-addr=0.0.0.0:9735`
- Increase backend RPC timeout:
    - `-bitcoin-rpcclienttimeout=180`
- Start LN after bitcoind RPC is ready (avoid `28 Loading block index…` window)

### 3.3 Node pubkeys (evidence)

CLN A (`cln_a`)

- pubkey: `0210d5edfed05c6ff94305e7a9758de2d96eb6ee626ba55100e6e6d7e0b7e60a54`
- alias: `SILENTTRINITY`

CLN B (`cln_b`)

- pubkey: `02cc31b3585a0a660649a2d39d772afb35e71277b2e4064b2c0bb99b21c831dcaa`
- alias: `CHILLYBEAM`

### 3.4 Peer connection A ↔ B

Connect A → B:

```
dockercomposeexeccln_alightning-cli--network=testnetconnect02cc31b3585a0a660649a2d39d772afb35e71277b2e4064b2c0bb99b21c831dcaa@cln_b:9735
dockercomposeexeccln_alightning-cli--network=testnetlistpeers
```

---

## 4) Funding CLN A on-chain (testnet faucet)

Generated on-chain addresses for CLN A:

```
dockercomposeexeccln_alightning-cli--network=testnetnewaddr
```

Addresses (CLN A):

- bech32: `tb1q28fvpay23nykl03ap2np98t90zffl734ew6kfa`
- p2tr: `tb1p92vt4mzzyr9j4vhjy6uswdlekkep3xkf38a7y84z3y94ry2lh64sgkv8d0`

Faucet used (coinfaucet.eu) — transaction created:

- amount: `0.00144825 tBTC`
- txid: `069cfb75a48803841830ff490a7fc38f4f4a2b8527dd78b33963b42f491ab630`

---

## 5) Liquidity Inspector + Payment Orchestrator tool

File: `tool/liquidity_orchestrator.py`

What it does:

- `status`: show bitcoind status + LN status (pubkey, warning, peers, funds count)
- `inspect`: list channel liquidity and compute outbound/inbound ratios (once channels exist)
- `check --amount-sat`: check if payer can send/receive a given amount (once channels exist)
- `invoice`: create invoice on payee node
- `pay`: pay invoice from payer and append JSONL log to `logs/attempts.jsonl`

Run:

```
pythontool\liquidity_orchestrator.pystatus
pythontool\liquidity_orchestrator.pyinspect
pythontool\liquidity_orchestrator.pycheck--amount-sat50000
```

Logs:

- `logs/attempts.jsonl` (one JSON object per payment attempt)

---

## Example output (tool status)

```
== Bitcoin node ==
{
  "chain": "test",
  "blocks": 1681765,
  "headers": 4879296,
  "verificationprogress": 0.1574833240740075,
  "initialblockdownload": true
}
{
  "connections": 10,
  "subversion": "/Satoshi:28.0.0/"
}

== Lightning nodes ==

[cln_a]
{
  "id": "0210d5edfed05c6ff94305e7a9758de2d96eb6ee626ba55100e6e6d7e0b7e60a54",
  "alias": "SILENTTRINITY",
  "network": "testnet",
  "blockheight": 545783,
  "num_peers": 1,
  "warning_bitcoind_sync": "Bitcoind is not up-to-date with network."
}
funds.outputs_count=0 channels_count=0

[cln_b]
{
  "id": "02cc31b3585a0a660649a2d39d772afb35e71277b2e4064b2c0bb99b21c831dcaa",
  "alias": "CHILLYBEAM",
  "network": "testnet",
  "blockheight": 559092,
  "num_peers": 1,
  "warning_bitcoind_sync": "Bitcoind is not up-to-date with network."
}
funds.outputs_count=0 channels_count=0
```

---

## Submission notes / current limitation (IBD)

Bitcoin Core is still in IBD (Initial Block Download) and building indexes (`verificationprogress < 1`).

Because the local node is behind the testnet tip, it may not have reached the block height containing the faucet transaction yet. As a result:

- `bitcoind getrawtransaction <TXID> 1` currently returns:
    - `error -5: No such mempool or blockchain transaction`
- CLN on-chain wallet does not yet show UTXOs (`listfunds.outputs_count = 0`)
- Channel funding is therefore pending until bitcoind catches up further.

Once the faucet UTXO becomes visible in `cln_a listfunds`, channels and payments are fully reproducible using the steps below.

---

## Channel opening steps (run once `cln_a listfunds` shows confirmed outputs)

### 1) Ensure LN nodes are up and connected

```
dockercomposeexeccln_alightning-cli--network=testnetgetinfo
dockercomposeexeccln_blightning-cli--network=testnetgetinfo
dockercomposeexeccln_alightning-cli--network=testnetconnect02cc31b3585a0a660649a2d39d772afb35e71277b2e4064b2c0bb99b21c831dcaa@cln_b:9735
dockercomposeexeccln_alightning-cli--network=testnetlistpeers
```

### 2) Confirm CLN A has on-chain funds

```
dockercomposeexeccln_alightning-cli--network=testnetlistfunds
```

### 3) Open channel #1 (A -> B)

```
dockercomposeexeccln_alightning-cli--network=testnetfundchannel02cc31b3585a0a660649a2d39d772afb35e71277b2e4064b2c0bb99b21c831dcaa100000
```

### 4) Open channel #2 (option: B -> A)

```
dockercomposeexeccln_blightning-cli--network=testnetconnect0210d5edfed05c6ff94305e7a9758de2d96eb6ee626ba55100e6e6d7e0b7e60a54@cln_a:9735
dockercomposeexeccln_blightning-cli--network=testnetfundchannel0210d5edfed05c6ff94305e7a9758de2d96eb6ee626ba55100e6e6d7e0b7e60a5450000
```

### 5) Payment demo + logging (tool)

```
pythontool\liquidity_orchestrator.pyinvoice--amount-sat5000--labeldemo1--description"demo payment"
# copy bolt11 from output:
pythontool\liquidity_orchestrator.pypay--amount-sat5000--bolt11<BOLT11>
```

---

## How to run (end-to-end)

Start infrastructure:

```
dockercomposeup-d
dockerps
```

Monitor bitcoind sync:

```
dockercomposeexecbitcoindbitcoin-cli-testnet-rpcuser=bitcoin-rpcpassword=bitcoingetblockchaininfo
```

---


























