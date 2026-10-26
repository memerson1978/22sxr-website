# 22sxr-website

Website and master booking calendar for 22 Saxonbury Road, Southbourne.

## Master calendar

Every hour, GitHub reads the Airbnb and Vrbo calendars, adds the direct bookings in
`data/direct-bookings.csv`, and publishes the results in `calendar/`:

| File | Who uses it | Contains |
|---|---|---|
| `calendar/for-airbnb.ics` | Imported by Airbnb | Vrbo + direct bookings |
| `calendar/for-vrbo.ics` | Imported by Vrbo | Airbnb + direct bookings |
| `calendar/master.ics` | Owner, morning check, cleaner | Every booking, labelled Airbnb, Vrbo or Direct |
| `calendar/availability.json` | Website availability calendar | Booked date ranges only |
| `calendar/status.json` | Morning check | Whether each feed was read, double bookings |

Only dates and channel names are ever published. Guest names, phone numbers and prices
stay in the private booking sheet. This repository is public, so keep it that way.

### Adding a direct booking

Add one line to `data/direct-bookings.csv` (arrival date, checkout date, type) and save.
The calendar rebuilds within a few minutes, and Airbnb and Vrbo close those dates the next
time they refresh their imports.

```
2027-02-12,2027-02-15,WEBS
```

Types: `WEBS` website, `SO` Simply Owners, `RTN` returning guest, `US` owner stay or maintenance.
Delete the line to cancel.

### Safety

If a platform feed cannot be read, or suddenly returns no future bookings, the last good
copy is kept, so a glitch never reopens booked dates. If two channels hold the same night,
the run is marked as failed and GitHub emails the owner.

### Settings

The Airbnb and Vrbo export links are stored as repository secrets
`AIRBNB_ICS_URL` and `VRBO_ICS_URL` (Settings, Secrets and variables, Actions).
