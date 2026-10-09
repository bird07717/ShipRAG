// Element Plus fills the time of a picked date range from `default-time`. Without it
// the end defaults to 00:00:00, so a range ending on a given day drops that whole day.
export const rangeDefaultTime: [Date, Date] = [
  new Date(2000, 0, 1, 0, 0, 0),
  new Date(2000, 0, 1, 23, 59, 59),
];
