import type { Metadata } from "next";
import "./globals.css";
import { AuthProvider } from "@/lib/auth";
import { Header } from "@/components/Header";
import { PartPromptProvider } from "@/components/PartPromptOverlay";

export const metadata: Metadata = {
  title: "LunaiCAD — Parametric CAD workspace",
  description:
    "Generate validated, manufacturable parametric CAD parts from a description. Inspect, edit, validate, and export STEP/STL.",
  openGraph: {
    title: "LunaiCAD — Parametric CAD workspace",
    description:
      "Generate validated, manufacturable parametric CAD parts from a plain-English description.",
    siteName: "LunaiCAD",
  },
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
          <PartPromptProvider>
            <Header />
            {/* Pages add their own container (.page); the Studio workspace is
                full-bleed. */}
            <main>{children}</main>
          </PartPromptProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
