import type { Metadata } from "next";
import "./globals.css";
import { AuthProvider } from "@/lib/auth";
import { Header } from "@/components/Header";

export const metadata: Metadata = {
  title: "SourceCAD — Parametric CAD from plain English",
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
          <main className="mx-auto max-w-7xl px-5 py-6">{children}</main>
        </AuthProvider>
      </body>
    </html>
  );
}
