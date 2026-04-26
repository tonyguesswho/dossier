import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Forward build-time env vars to SSR runtime. Vercel injects these into the
  // SSR Lambda automatically; Amplify's `environmentVariables` map only
  // reaches the build container — without this bridge clerkMiddleware throws
  // "Missing secretKey" on every request. Vars are inlined into the .next/
  // server bundle at build time (server-only, never shipped to the browser).
  env: {
    CLERK_SECRET_KEY: process.env.CLERK_SECRET_KEY,
  },
};

export default nextConfig;
