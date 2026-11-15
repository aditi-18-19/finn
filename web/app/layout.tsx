import type { Metadata, Viewport } from 'next';
import './globals.css';
import PWARegister from './pwa-register';

export const metadata: Metadata = {
  title: 'FINN — Investment Intelligence',
  description: 'Evidence-led investment intelligence for your portfolio.',
  applicationName: 'FINN',
  manifest: '/manifest.webmanifest',
  appleWebApp: { capable: true, statusBarStyle: 'default', title: 'FINN' },
};
export const viewport: Viewport = { themeColor: '#0b36a1', width: 'device-width', initialScale: 1, maximumScale: 1 };

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body><PWARegister />{children}</body></html>;
}
