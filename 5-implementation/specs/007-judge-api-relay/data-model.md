# Data model

See [relay contract](../../contracts/relay.md) for transport/settings. One Firestore document stores `total_calls`, `minute`, and `minute_calls`. Transactions check total/current-minute limits then increment both counters. Failures consume reservations. Secret values never enter Firestore.
