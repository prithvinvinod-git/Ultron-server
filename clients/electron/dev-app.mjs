import { spawn } from "node:child_process";

const url = process.env.ULTRON_DEV_SERVER_URL ?? "http://localhost:8080";
const env = { ...process.env, ULTRON_DEV_SERVER_URL: url };

function run(command, args) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      stdio: "inherit",
      shell: process.platform === "win32",
      env,
    });
    child.on("error", reject);
    child.on("close", (code) => resolve(code ?? 1));
  });
}

const build = await run("npm", ["run", "build"]);
if (build !== 0) process.exit(build);

const before = Date.now();
try {
  const res = await fetch(url + "/", { signal: AbortSignal.timeout(4000) });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
} catch (error) {
  console.error(
    `\n[dev:app] dev server not reachable at ${url} (${error.message}).\n` +
      `Start it in another terminal:  cd UIPROTO  &&  npm run dev\n`,
  );
  process.exit(1);
}
void before;

const electron = await run("npx", ["electron", "."]);
process.exit(electron);
