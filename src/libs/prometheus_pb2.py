"""
Vendored Prometheus Remote Write protobuf definitions.
Based on https://github.com/prometheus/prometheus/blob/main/prompb/remote.proto

This is a minimal implementation containing only what we need for remote write.
"""

# Protobuf message types as Python dataclasses for simplicity
from dataclasses import dataclass, field
from typing import List


@dataclass
class Label:
    """Prometheus label (key-value pair)."""
    name: str
    value: str


@dataclass
class Sample:
    """Prometheus sample (value + timestamp)."""
    value: float
    timestamp: int  # milliseconds since epoch


@dataclass
class TimeSeries:
    """Prometheus time series (labels + samples)."""
    labels: List[Label] = field(default_factory=list)
    samples: List[Sample] = field(default_factory=list)


@dataclass
class WriteRequest:
    """Prometheus Remote Write request."""
    timeseries: List[TimeSeries] = field(default_factory=list)

    def SerializeToString(self) -> bytes:
        """
        Serialize to Prometheus protobuf wire format.

        Wire format (simplified protobuf encoding):
        - WriteRequest: repeated TimeSeries (field 1)
        - TimeSeries: repeated Label (field 1), repeated Sample (field 2)
        - Label: string name (field 1), string value (field 2)
        - Sample: double value (field 1), int64 timestamp (field 2)
        """
        output = bytearray()

        for ts in self.timeseries:
            # Serialize TimeSeries
            ts_bytes = self._serialize_timeseries(ts)
            # Field 1 (timeseries), wire type 2 (length-delimited)
            output.extend(self._encode_key(1, 2))
            output.extend(self._encode_varint(len(ts_bytes)))
            output.extend(ts_bytes)

        return bytes(output)

    def _serialize_timeseries(self, ts: TimeSeries) -> bytes:
        """Serialize a TimeSeries message."""
        output = bytearray()

        # Serialize labels (field 1)
        for label in ts.labels:
            label_bytes = self._serialize_label(label)
            output.extend(self._encode_key(1, 2))
            output.extend(self._encode_varint(len(label_bytes)))
            output.extend(label_bytes)

        # Serialize samples (field 2)
        for sample in ts.samples:
            sample_bytes = self._serialize_sample(sample)
            output.extend(self._encode_key(2, 2))
            output.extend(self._encode_varint(len(sample_bytes)))
            output.extend(sample_bytes)

        return bytes(output)

    def _serialize_label(self, label: Label) -> bytes:
        """Serialize a Label message."""
        output = bytearray()

        # name (field 1, string)
        name_bytes = label.name.encode('utf-8')
        output.extend(self._encode_key(1, 2))
        output.extend(self._encode_varint(len(name_bytes)))
        output.extend(name_bytes)

        # value (field 2, string)
        value_bytes = label.value.encode('utf-8')
        output.extend(self._encode_key(2, 2))
        output.extend(self._encode_varint(len(value_bytes)))
        output.extend(value_bytes)

        return bytes(output)

    def _serialize_sample(self, sample: Sample) -> bytes:
        """Serialize a Sample message."""
        import struct
        output = bytearray()

        # value (field 1, double/fixed64, wire type 1)
        output.extend(self._encode_key(1, 1))
        output.extend(struct.pack('<d', sample.value))

        # timestamp (field 2, int64, wire type 0)
        output.extend(self._encode_key(2, 0))
        output.extend(self._encode_varint(sample.timestamp))

        return bytes(output)

    @staticmethod
    def _encode_key(field_number: int, wire_type: int) -> bytes:
        """Encode protobuf field key (field_number << 3 | wire_type)."""
        return WriteRequest._encode_varint((field_number << 3) | wire_type)

    @staticmethod
    def _encode_varint(value: int) -> bytes:
        """Encode integer as protobuf varint."""
        output = bytearray()
        while value > 0x7f:
            output.append((value & 0x7f) | 0x80)
            value >>= 7
        output.append(value & 0x7f)
        return bytes(output)
