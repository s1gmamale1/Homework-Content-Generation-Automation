import type { Transport } from "./types";

export function normalizeProviderTransport(args: {
  transport: Transport;
  apiSupported: boolean;
  apiOnly: boolean;
  apiFleetOk: boolean;
}): Transport {
  if (args.apiOnly) return "api";
  if (args.transport === "api" && (!args.apiSupported || !args.apiFleetOk)) {
    return "cli";
  }
  return args.transport;
}
