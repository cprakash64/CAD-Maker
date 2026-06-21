import type { Metadata } from "next";
import "./globals.css";
import { AuthProvider } from "@/lib/auth";
import { Header } from "@/components/Header";

export const metadata: Metadata = {
  title: "CAD Maker — Parametric CAD workspace",
  description:
    "Generate validated, manufacturable parametric CAD parts from a description. Inspect, edit, validate, and export STEP/STL.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>
        <AuthProvider>
          <Header />
          {/* Pages add their own container (.page); the Studio workspace is
              full-bleed. */}
          <main>{children}</main>
        </AuthProvider>
      </body>
    </html>
  );
}
