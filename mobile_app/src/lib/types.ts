export type ScreenName =
  | 'Landing'
  | 'MyRequests'
  | 'WhoNeedsHelp'
  | 'CanYouExplain'
  | 'Chatbot'
  | 'PhotoUpload'
  | 'ConfirmReport'
  | 'Success';

export type Victim = '' | 'Me' | 'SomeoneElse';

export interface LatLng {
  latitude: number;
  longitude: number;
}

/** A report as stored locally. Field names match the raw_reports DB table. */
export interface Report {
  id: string; // server UUID when synced, local id otherwise
  report_id: string; // RPT-YYYYMMDD-XXXX
  user_behavior: 'victim' | 'observer';
  can_communicate: boolean;
  event_define: string;
  thumbnail: string | null; // small JPEG data URL for the list
  gps_location: LatLng;
  report_date: string; // ISO timestamp
  synced: boolean;
  isInCluster?: boolean;
  cluster_id?: string;
}

export interface PhotoInfo {
  width: number;
  height: number;
  thumbnail: string; // JPEG data URL
}

/** Report being filled in through the wizard. */
export interface Draft {
  victim: Victim;
  event_define: string;
  photo: File | null;
  photoUrl: string | null; // object URL for previews
  photoInfo: PhotoInfo | null;
}

export interface NearbyCluster {
  cluster_id: string;
  event_type: string | null;
  report_count: number;
  severity_avg: number | null;
  message: string;
}
