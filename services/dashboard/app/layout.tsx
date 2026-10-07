import type { Metadata } from "next";

// Inter, self-hosted as a variable font with its optical-size axis. Bundled
// from node_modules at build time, so the console never asks a font CDN for
// anything - it has to render on a network that reaches nothing but itself.
import "@fontsource-variable/inter/opsz.css";
import "@fontsource-variable/inter/opsz-italic.css";

import { AppFrame } from "@/components/AppFrame";
import "./globals.css";

export const metadata: Metadata = {
  title: "Vigentra — Unified AI Video Intelligence for Safer Cities",
  description:
    "One CCTV registry across Traffic Police and Municipal Corporation cameras, with a GIS "
    + "map, live video, object detection, number-plate reading and cross-camera vehicle tracing.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <AppFrame>{children}</AppFrame>
      </body>
    </html>
  );
}
