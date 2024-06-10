# pretix-steamroller

Create pretix events from a declarative configuration file.


## Example

This creates a simple test event equivalent to what the quick start GUI will create for you.

```yaml
event:
  name:
    en: Example Conference
  slug: ExConf
  testmode: true
  date_from: '2024-12-27T00:00:00Z'
  date_to: '2024-12-31T00:00:00Z'
  location:
    en: Example Conference Center, Test City, 12345
  plugins:
  - pretix.plugins.sendmail
  - pretix.plugins.statistics
  - pretix.plugins.ticketoutputpdf
  - pretix_fakepayment
  - pretix_passbook

settings:
  imprint_url: https://example.com/imprint.aspx
  locales:
  - en
  locale: en
  waiting_list_enabled: true
  contact_mail: noreply@example.com
  ticket_download: true

taxrules: []

categories:
- name:
    en: Tickets
  internal_name: null
  description:
    de: ''
  is_addon: false

items:
- category: !<ref> [categories, 0, id]
  name:
    en: Regular ticket
  sales_channels:
  - web
  - pretixpos
  - resellers
  default_price: '35.00'
  admission: true
  personalized: true
- category: !<ref> [categories, 0, id]
  name:
    en: Reduced ticket
  sales_channels:
  - web
  - pretixpos
  - resellers
  default_price: '29.00'
  admission: true
  personalized: true

quotas:
- name: Regular ticket
  size: 100
  items:
  - !<ref> [items, 0, id]
- name: Reduced ticket
  size: 50
  items:
  - !<ref> [items, 1, id]

item_meta_properties: []
questions: []
vouchers: []
```


## Usage
All command line parameters are visible in the `python steamroll.py --help` output.

### Auth

Create a file named `auth.yml` to store access tokens, e.g.:

```yaml
"https://staging.pretix.eu":
  Authorization: Token YOUR_AUTH_TOKEN_HERE

```

### Create Event

To create an event at https://staging.pretix.eu/MyOrganizerName/MyEventName from a config file named `my_config_file.yml`:

```shell
python steamroll.py event create staging.pretix.eu MyOrganizerName MyEventName --file my_config_file.yml
```

If you want to override an event that already exists, add `--force true`. 
NOTE: This will delete all existing data in the event (orders, etc).


### Export Event Config

To retrieve the event config for https://staging.pretix.eu/MyOrganizerName/MyEventName and store in 
`organizers_MyOrganizerName_events_MyEventName.yml`:

```shell
python steamroll.py event fetch staging.pretix.eu MyOrganizerName MyEventName
```



