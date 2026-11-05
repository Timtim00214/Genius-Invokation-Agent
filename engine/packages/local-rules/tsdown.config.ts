import { defineConfig } from "tsdown";

export default defineConfig({
  platform: "neutral",
  entry: {
    index: "./src/index.ts",
  },
  sourcemap: true,
  dts: !process.env.NO_TYPING,
  minify: true,
  target: false,
});
