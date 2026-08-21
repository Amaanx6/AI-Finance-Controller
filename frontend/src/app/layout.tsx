import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "AI Finance Controller",
  description: "Transaction reconciliation with AI-powered exception handling",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
