// Argo Exo Transaction Guardian: the CRE workflow `exo`.
// Thin wiring only. Every decision lives in src/lib (bun-tested); this file adapts the CRE runtime to GuardPorts.
//   trigger 0 = guard  (HTTP, §4.3 request → §4.3 result JSON; writes a kind-1 report on approve; a refusal writes nothing)
//   trigger 1 = freeze (HTTP {"reason"} → kind-3 report)
import {
  bytesToHex, consensusIdenticalAggregation, decodeJson, EVMClient, getNetwork, handler, handlerInTee, hexToBase64,
  HTTPCapability, HTTPClient, Runner, text, TxStatus,
  type HTTPPayload, type HTTPSendRequester, type Runtime, type TeeRuntime,
} from "@chainlink/cre-sdk";
import { EVM_PB } from "@chainlink/cre-sdk/pb";
import { ConfigSchema, type Config } from "./src/lib/config";
import { runFreeze, runGuard, SECRET_IDS, type GuardPorts, type Secrets } from "./src/lib/guard";
import { judgeFromReply, toSdkRequest, type HttpReply, type HttpRequest } from "./src/lib/http";
import { parseJudge } from "./src/lib/judge";
import type { Hex } from "./src/lib/types";

// ─── CONFIDENTIAL SWITCH ─────────────────────────────────────────────────────────────────────────────────────────
// false: plain `handler` (DON mode). HTTP goes through runInNodeMode + identical consensus; secrets via
//        runtime.getSecrets. Works in `cre workflow simulate` without beta access.
// true:  `handlerInTee` + TeeRuntime.getSecrets (Confidential Workflows, private beta). The guard pipeline,
//        the keys and the policy stay inside the enclave; only the report payload crosses to the DON
//        (runtime.usingTheDons()). Flip only once beta access is confirmed (03-guardian Task 0 Step 3).
const USE_TEE = false;
// ──────────────────────────────────────────────────────────────────────────────────────────────────────────────

const nowSeconds = (rt: { now(): Date }) => Math.floor(rt.now().getTime() / 1000);
const replyOf = (status: number, body: string): HttpReply => ({ status, body });
const readSecrets = (rt: Runtime<Config> | TeeRuntime<Config>): Secrets => {
  const s = rt.getSecrets(SECRET_IDS.map((id) => ({ id }))).result();
  return Object.fromEntries(SECRET_IDS.map((id) => [id, s[id].value])) as Secrets;
};

/** Sign `payload` as an EVM report on the DON and deliver it to ExoModule. Throws unless the write succeeded. */
function deliverReport(don: Runtime<Config>, payload: Hex): string {
  const cfg = don.config;
  const network = getNetwork({ chainFamily: "evm", chainSelectorName: cfg.chainSelectorName });
  if (!network) throw new Error(`network ${cfg.chainSelectorName} not found`);
  const report = don.report({ encodedPayload: hexToBase64(payload), encoderName: "evm", signingAlgo: "ecdsa", hashingAlgo: "keccak256" }).result();
  const w = new EVMClient(network.chainSelector.selector)
    .writeReport(don, { receiver: cfg.module, report, gasConfig: { gasLimit: cfg.gasLimit } }).result();
  if (w.txStatus !== TxStatus.SUCCESS) throw new Error(`report tx status ${w.txStatus}`);
  // Optional field (absent in a dry run); txStatus and the receiver status are independent, so check both.
  if (w.receiverContractExecutionStatus === EVM_PB.ReceiverContractExecutionStatus.REVERTED) throw new Error("ExoModule.onReport reverted");
  return bytesToHex(w.txHash ?? new Uint8Array(32));
}

/** DON mode: every HTTP call runs on each node and must agree byte-for-byte. A reviewer reply is reduced to the
 *  parsed Judgement on the node first; nodes that disagree fall back to the fail-closed "high". */
function donPorts(rt: Runtime<Config>): GuardPorts {
  const http = new HTTPClient();
  const fetchOnNode = (sr: HTTPSendRequester, r: HttpRequest): string => {
    const res = sr.sendRequest(toSdkRequest(r)).result();
    return JSON.stringify(replyOf(res.statusCode, text(res)));
  };
  const judgeOnNode = (sr: HTTPSendRequester, r: HttpRequest): string => {
    const res = sr.sendRequest(toSdkRequest(r)).result();
    return JSON.stringify(judgeFromReply(replyOf(res.statusCode, text(res))));
  };
  const FAIL = JSON.stringify(parseJudge(""));
  return {
    nowSeconds: () => nowSeconds(rt),
    secrets: () => readSecrets(rt),
    post: (r) => JSON.parse(http.sendRequest(rt, fetchOnNode, consensusIdenticalAggregation<string>())(r).result()),
    judge: (r) => JSON.parse(http.sendRequest(rt, judgeOnNode, consensusIdenticalAggregation<string>().withDefault(FAIL))(r).result()),
    writeReport: (p) => deliverReport(rt, p),
    log: (m) => rt.log(m),
  };
}

/** TEE mode: HTTP runs natively inside the enclave (HTTPClient's TeeRuntime overload); the report crosses out. */
function teePorts(rt: TeeRuntime<Config>): GuardPorts {
  const http = new HTTPClient();
  const post = (r: HttpRequest): HttpReply => {
    const res = http.sendRequest(rt, toSdkRequest(r)).result();
    return replyOf(res.statusCode, text(res));
  };
  return {
    nowSeconds: () => nowSeconds(rt),
    secrets: () => readSecrets(rt),
    post,
    judge: (r) => judgeFromReply(post(r)),
    writeReport: (p) => deliverReport(rt.usingTheDons(), p),
    log: (m) => rt.log(m), // only called when config.debug (never in a production confidential workflow)
  };
}

const input = (payload: HTTPPayload): unknown => {
  try { return decodeJson(payload.input); } catch { return undefined; } // undecodable → runGuard refuses
};

export const onGuard = (rt: Runtime<Config>, payload: HTTPPayload): string =>
  JSON.stringify(runGuard(input(payload), rt.config, donPorts(rt)));

export const onGuardTee = (rt: TeeRuntime<Config>, payload: HTTPPayload): string =>
  JSON.stringify(runGuard(input(payload), rt.config, teePorts(rt)));

export const onFreeze = (rt: Runtime<Config>, payload: HTTPPayload): string =>
  JSON.stringify(runFreeze(input(payload), rt.config, (p) => deliverReport(rt, p)));

// The HTTP triggers take {} for simulation only. TODO(konrad): a deployed workflow must list the bridge's signing
// address in authorizedKeys ({ type: "KEY_TYPE_ECDSA_EVM", publicKey: "0x…" }), or the DON rejects the trigger.
export const initWorkflow = (_config: Config) => {
  const http = new HTTPCapability();
  return [
    USE_TEE ? handlerInTee(http.trigger({}), onGuardTee, {}) : handler(http.trigger({}), onGuard),
    handler(http.trigger({}), onFreeze),
  ];
};

export async function main() {
  const runner = await Runner.newRunner<Config>({ configSchema: ConfigSchema });
  await runner.run(initWorkflow);
}
