import type { Metadata, Viewport } from "next";
import { Fraunces, Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { Providers } from "./providers";

// Inter for interface text (optical sizing keeps 12px labels and 30px numbers equally crisp), Fraunces
// for titles (a variable serif whose optical-size axis stays sturdy at 18px and elegant at 48px), and
// JetBrains Mono only for figures that must align.
const sans = Inter({ variable: "--font-ui", subsets: ["latin"], axes: ["opsz"], display: "swap" });
const mono = JetBrains_Mono({ variable: "--font-code", subsets: ["latin"], display: "swap" });
const display = Fraunces({ variable: "--font-title", subsets: ["latin"], axes: ["opsz", "SOFT"], style: ["normal", "italic"], display: "swap" });

export const metadata: Metadata = {
  title: { default: "OpenKG", template: "%s · OpenKG" },
  description: "Research agents that build evidence-backed knowledge graphs and keep them current.",
};
export const viewport: Viewport = {
  themeColor: [{ media: "(prefers-color-scheme: dark)", color: "#0a0c11" }, { color: "#f4f2ec" }],
};

// Applied before paint so the stored theme never flashes.
const themeScript = `try{var t=localStorage.getItem("openkg-theme");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t;else document.documentElement.dataset.theme=matchMedia("(prefers-color-scheme: light)").matches?"light":"dark"}catch(e){document.documentElement.dataset.theme="dark"}`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning className={`${sans.variable} ${mono.variable} ${display.variable} h-full`}>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="h-full">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
