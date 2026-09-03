import type { Metadata } from "next";

import { AppFrame } from "@/components/AppFrame";
import "./globals.css";

export const metadata: Metadata = {
  title: "Vigentra — Unified AI Video Intelligence for Safer Cities",
  description:
    "A federated CCTV registry with GIS, brokered live video, edge ANPR and cross-camera "
    + "vehicle tracing across independently owned Traffic Police and Municipal Corporation "
    + "estates. Footage stays with the owning department and is brokered per audited session. "
    + "Synthetic demonstration data.",
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
