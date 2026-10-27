import type { CapacitorConfig } from '@capacitor/cli';
const config: CapacitorConfig = {
  appId: 'studio.sitemirror.app',
  appName: 'SiteMirror Studio',
  webDir: '../web',
  server: {
    // Production mobile builds should point this at your deployed SiteMirror API/UI.
    // url: 'https://sitemirror.example.com',
    androidScheme: 'https'
  }
};
export default config;
