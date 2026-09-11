import type { Metadata } from "next";

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
