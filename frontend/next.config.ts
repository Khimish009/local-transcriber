import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Standalone output keeps the runtime image small (no node_modules copy needed).
  output: "standalone",
  reactStrictMode: true,
};

export default nextConfig;
