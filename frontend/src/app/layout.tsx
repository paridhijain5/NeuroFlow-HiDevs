import type { Metadata } from "next";
import "./globals.css";
import Providers from "./providers";
import Nav from "@/components/Nav";

export const metadata: Metadata = {
  title: "NeuroFlow",
  description: "NeuroFlow AI platform dashboard",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="min-h-screen bg-slate-50 text-slate-900" style={{ colorScheme: "light" }}>
          <Providers>
            <Nav />
            <main className="mx-auto max-w-7xl px-4 py-6">{children}</main>
          </Providers>
        </div>
      </body>
    </html>
  );
}
