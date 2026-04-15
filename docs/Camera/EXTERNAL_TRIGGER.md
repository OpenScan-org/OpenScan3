# Camera via External Trigger

This note documents the default GPIO pin used for an external camera trigger on
the supported shield variants.

## Pin Mapping

- `GreenShield`: GPIO `10`
- `BlackShield`: GPIO `5`

## Wiring Note

If you connect an external trigger source, make sure the source is wired against
the correct shield variant:

- use GPIO `10` on `GreenShield`
- use GPIO `5` on `BlackShield`

If needed, document the corresponding physical header pins separately for the
specific hardware revision, because BCM numbering and board pin positions are
not interchangeable.

## Electrical Note

On both boards, the external trigger line is routed through an optocoupler.
That means the Raspberry Pi GPIO controls the trigger indirectly and the
electrical characteristics on the external side depend on the shield hardware
and the connected device.

In practice, the external trigger circuit may require its own supply voltage on
the isolated side. Do not assume that the Raspberry Pi GPIO pin itself powers
the external trigger input. Verify the required voltage, polarity, and current
against the shield schematic and the camera or device you want to trigger.

## Trigger Configuration

The trigger API and configuration use the following fields:

- `enabled`: Enables or disables the trigger. If set to `false`, the firmware
  will refuse to fire it.
- `pin`: GPIO pin used for the trigger output.
- `active_level`: Defines which logic level is the active pulse:
  `active_high` means the line goes high during the pulse, `active_low` means
  the line goes low during the pulse.
- `pulse_width_ms`: Duration of the active pulse in milliseconds.

## Trigger Usage

When a trigger is fired, the firmware:

1. sets the trigger line to its active level
2. keeps it active for `pulse_width_ms`
3. returns the line to its idle level

The API also supports:

- `pre_trigger_delay_ms`: wait time before the pulse
- `post_trigger_delay_ms`: wait time after the pulse

This is useful if the external device needs setup time before the trigger pulse
or some settling time afterwards.


## API Example

Get the current trigger status:

```http
GET /triggers/camera
```

Example response:

```json
{
  "name": "camera",
  "busy": false,
  "settings": {
    "enabled": true,
    "pin": 10,
    "active_level": "active_high",
    "pulse_width_ms": 100
  },
  "last_triggered_at": null,
  "last_completed_at": null,
  "last_duration_ms": null
}
```

Update the trigger settings:

```http
PATCH /triggers/camera/settings
Content-Type: application/json
```

```json
{
  "pin": 10,
  "active_level": "active_high",
  "pulse_width_ms": 150
}
```

Fire one trigger pulse with optional delays:

```http
POST /triggers/camera/trigger
Content-Type: application/json
```

```json
{
  "pre_trigger_delay_ms": 50,
  "post_trigger_delay_ms": 200
}
```

Example response:

```json
{
  "name": "camera",
  "triggered_at": "2026-04-14T12:00:00.000000",
  "completed_at": "2026-04-14T12:00:00.150000",
  "duration_ms": 150
}
```
