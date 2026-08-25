/** Next.js configuration for the Sentinel dashboard. */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  output: "standalone",
  experimental: { serverComponentsExternalPackages: [] },
};

export default nextConfig;
