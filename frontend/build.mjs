import { build } from "esbuild";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const out = resolve(here, "dist");
const secret = /gsk_[A-Za-z0-9]{10,}|[0-9]{8,12}:[A-Za-z0-9_-]{30,}|ck_[A-Za-z0-9_-]{10,}|ak_[A-Za-z0-9_-]{10,}|SECRET_KEY|MASTER_KEY/;

await mkdir(out, { recursive: true });

for (const name of ["app.js", "widget.js"]) {
  const source = await readFile(resolve(here, name), "utf8");
  const hit = source.match(secret);
  if (hit) {
    console.error(`[стоп] в ${name} найден похожий на секрет фрагмент: ${hit[0].slice(0, 8)}…`);
    process.exit(1);
  }
  const result = await build({
    stdin: { contents: source, loader: "js", resolveDir: here },
    minify: true,
    legalComments: "none",
    target: "es2020",
    write: false,
  });
  await writeFile(resolve(out, name), result.outputFiles[0].text, "utf8");
  const saved = Math.round((1 - result.outputFiles[0].text.length / source.length) * 100);
  console.log(`${name}: ${source.length} → ${result.outputFiles[0].text.length} байт (−${saved}%)`);
}

console.log("Готово: frontend/dist/app.js и frontend/dist/widget.js");