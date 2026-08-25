import type { Metadata } from "next";

import { AppFrame } from "@/components/AppFrame";
import "./globals.css";

export const metadata: Metadata = {
  title: "Sentinel — CCTV Asset Registry",
  description:
    "Module 1: metadata-only federated CCTV registry for Traffic Police and Municipal Corporation camera assets. Footage remains with the owning department. Synthetic demonstration data.",
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
