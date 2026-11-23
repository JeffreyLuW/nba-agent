import type { ReactNode } from "react";
import "./globals.css";

export const metadata = { title: "NBA Agent" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
