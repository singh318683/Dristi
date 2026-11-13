import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.ikshana.drishti',
  appName: 'Drishti',
  webDir: '../web',
  server: {
    url: 'https://dristi-six.vercel.app',
    cleartext: false,
    androidScheme: 'https',
  },
  ios: {
    backgroundColor: '#FAF7F2',
  },
};

export default config;
